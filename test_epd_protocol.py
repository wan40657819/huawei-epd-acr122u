import unittest
from pathlib import Path
from epd_protocol import parse_device_info,validate_target,build_transfer_plan,require_success,parse_refresh_status,split_compressed
from epd_dependencies import ensure_dependencies
from epd_protocol import load_pin
import epd_protocol
from tempfile import TemporaryDirectory

D1=bytes.fromhex('A007F0C72006000228 A10700120030FFFFFF B10102 B20114 B30100 C004201EA001 C10412547152 D10701200000000000 9000')

class EpdProtocolTests(unittest.TestCase):
    def test_refresh_power_delay_encoding(self):
        import epd_protocol
        builder=getattr(epd_protocol,'build_refresh_command',None)
        self.assertIsNotNone(builder,'Configurable D4 power-on delay is missing')
        self.assertEqual(builder(500),bytes.fromhex('f0d4858000'))
        self.assertEqual(builder(2000),bytes.fromhex('f0d4948000'))
        for bad in (0,-100,150,12800,2000.0,True):
            with self.subTest(delay=bad),self.assertRaises(ValueError): builder(bad)
    def test_local_pin_config(self):
        with TemporaryDirectory() as d:
            path=Path(d)/'device.json'
            self.assertEqual(load_pin(path),epd_protocol.DEFAULT_PIN)
            path.write_text('{"pin_hex":"01 02 03 04 05"}',encoding='utf-8')
            self.assertEqual(load_pin(path),bytes.fromhex('0102030405'))
            path.write_text('{"pin_hex":"00"}',encoding='utf-8')
            with self.assertRaises(ValueError): load_pin(path)
    def test_d1(self):
        info=parse_device_info(D1)
        self.assertEqual((info.width,info.height,info.colors),(552,768,4))
        self.assertTrue(info.compression)
        validate_target(info,bytes.fromhex('12547152'))
        for bad in (b'\x64\x01',D1[:-3]+b'\x90\x00',D1[:-2]+D1[:9]+b'\x90\x00'):
            with self.assertRaises(ValueError): parse_device_info(bad)
        with self.assertRaises(ValueError): validate_target(info,b'bad!')

    def test_other_uid_with_same_profile_is_accepted(self):
        from dataclasses import replace
        uid=bytes.fromhex('01020304')
        response=D1.replace(bytes.fromhex('12547152'),uid)
        info=parse_device_info(response)
        validate_target(info,uid)
        with self.assertRaisesRegex(ValueError,'UID'): validate_target(info,bytes.fromhex('05060708'))
        for changed in (replace(info,width=400),replace(info,colors=2),replace(info,compression=False)):
            with self.assertRaises(ValueError): validate_target(changed,uid)

    def test_packet_boundaries(self):
        for n,count in ((249,1),(250,1),(251,2),(2012,9)):
            packets=split_compressed(bytes([0x37])*n,5)
            self.assertEqual(len(packets),count)
            self.assertEqual([p.subpacket for p in packets],list(range(count)))
            for i,p in enumerate(packets):
                self.assertEqual(p.inf[:3],bytes.fromhex('F0D300'))
                self.assertEqual(p.inf[3],int(i==count-1))
                self.assertEqual(p.inf[4],len(p.inf)-5)
                self.assertEqual(p.inf[5:7],bytes([5,i]))
            self.assertLessEqual(max(len(p.inf) for p in packets),257)

    def test_lzo_plan_and_exact_block_sizes(self):
        ensure_dependencies(lzo_required=True)
        import lzokay
        data=bytes(range(256))*414 # 105984 bytes
        plan=build_transfer_plan(data)
        self.assertLessEqual(max(len(p.inf) for p in plan.packets),257)
        self.assertEqual(plan.blocks,53)
        reconstructed=bytearray()
        for block in range(53):
            compressed=b''.join(p.inf[7:] for p in plan.packets if p.block==block)
            reconstructed.extend(lzokay.decompress(compressed,2000 if block<52 else 1984))
        self.assertEqual(reconstructed, data)
        last=b''.join(p.inf[7:] for p in plan.packets if p.block==52)
        # A padded 2000-byte LZO stream must fail with the true 1984-byte capacity.
        self.assertEqual(lzokay.decompress(last,1984),data[104000:])
        with self.assertRaises(lzokay.OutputOverrunError): lzokay.decompress(last,1983)

    def test_fixed_application_stride_and_complete_stream(self):
        import random
        data=random.Random(17).randbytes(105984)
        plan=build_transfer_plan(data)
        # Keep device application stride; short RF segmentation is a separate layer.
        for packet in plan.packets:
            if not packet.last_in_block: self.assertEqual(len(packet.inf[7:]),250)
        ensure_dependencies(lzo_required=True)
        import lzokay
        restored=b''.join(lzokay.decompress(b''.join(p.inf[7:] for p in plan.packets if p.block==i),2000 if i<52 else 1984) for i in range(53))
        self.assertEqual(restored,data)

    def test_sw(self):
        self.assertEqual(require_success(b'hello\x90\x00','test'),b'hello')
        self.assertFalse(parse_refresh_status(bytes.fromhex('019000')))
        self.assertTrue(parse_refresh_status(bytes.fromhex('009000')))
        for bad in (b'',bytes.fromhex('9000'),bytes.fromhex('029000'),bytes.fromhex('006985')):
            with self.assertRaises(ValueError): parse_refresh_status(bad)
