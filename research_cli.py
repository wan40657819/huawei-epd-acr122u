"""Activation-only hardware tests, offline INF preview, explicit confirmed research."""
import argparse
import datetime as dt
import json
from pathlib import Path
import time
import wakeup_probe as probe
from iso_dep import Block,encode,describe
from pn532_iso_dep import PN532IsoDep


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='mode',required=True)
    activation=commands.add_parser('activate',help='Activate then hold reader RF; no application exchange')
    activation.add_argument('--port',required=True)
    activation.add_argument('--hold-seconds',type=float,default=5)
    activation.add_argument('--runs',type=int,choices=range(1,11),default=1)
    preview=commands.add_parser('preview',help='Offline framing only; never opens a COM port')
    preview.add_argument('--inf',required=True,help='Exact opaque INF hex')
    preview.add_argument('--fsc',type=int,choices=[16,24,32,40,48,64,96,128,256],default=256)
    preview.add_argument('--cid',type=int,choices=[0])
    send=commands.add_parser('send',help='Only for an externally verified, explicitly authorized read-only INF')
    send.add_argument('--port',required=True)
    send.add_argument('--inf',required=True)
    send.add_argument('--evidence',required=True,help='Capture location or documented protocol source and read-only rationale')
    send.add_argument('--confirmed-read-only',required=True,action='store_true')
    args=parser.parse_args(argv)
    inf=None
    if args.mode in ('preview','send'):
        try:
            inf=bytes.fromhex(args.inf)
        except ValueError:
            parser.error('INF is not valid hexadecimal bytes')
        if not inf or len(inf)>65536:
            parser.error('CLI INF must contain 1..65536 explicitly supplied bytes')
    if args.mode=='preview':
        size=args.fsc-3-int(args.cid is not None)
        chunks=[inf[i:i+size] for i in range(0,len(inf),size)]
        frames=[]
        for index,chunk in enumerate(chunks):
            raw=encode(Block('I',bn=index%2,chaining=index<len(chunks)-1,cid=args.cid,inf=chunk))
            frames.append(dict(raw=raw.hex(' '),**describe(raw)))
        print(json.dumps(dict(mode='offline preview; assumes successful ACKs between fragments',
                              frames=frames),indent=2))
        return 0
    if args.mode=='activate' and not 0<=args.hold_seconds<=60:
        parser.error('hold-seconds must be within 0..60')
    if args.mode=='send' and not args.evidence.strip():
        parser.error('Evidence/rationale must be nonempty; no built-in command is assumed read-only')
    runs=args.runs if args.mode=='activate' else 1
    for run in range(runs):
        path=Path(__file__).resolve().parent/'logs'/'stage2'/dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        probe.start_logging(path)
        probe.log('research_session',mode=args.mode,run=run+1,parameters=vars(args),
                  evidence='real hardware; application success not implied by activation')
        session=None
        try:
            session=PN532IsoDep(probe.Reader(args.port))
            session.activate()
            if args.mode=='activate':
                deadline=time.monotonic()+args.hold_seconds
                while True:
                    session.check_field()
                    remaining=deadline-time.monotonic()
                    if remaining<=0: break
                    time.sleep(min(1,remaining))
                probe.log('activation_hold_complete',hold_seconds=args.hold_seconds,
                          application_frames_sent=0,rf_on=session.rf_on,
                          target_liveness_after_idle='not probed')
            else:
                # Deliberately no command dictionary, APDU assumption, status-word interpretation,
                # scanning, automatic application replay, or default INF.
                reply=session.exchange(inf)
                probe.log('opaque_application_response',inf=reply.hex(' '),
                          meaning='uninterpreted; transport delivery does not prove command semantics')
        except Exception as exc:
            probe.log('research_failure',error=str(exc),type=type(exc).__name__)
            raise
        finally:
            if session is not None: session.close()
            probe.log('research_log_saved',path=str(path))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
