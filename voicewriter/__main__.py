import argparse
import os
import sys

parser = argparse.ArgumentParser(prog="voicewriter")
parser.add_argument("--remote-only", action="store_true",
                    help="run only the remote microphone: no PC microphone, bar or typing")
parser.add_argument("--data-dir", help="keep settings, history and logs here instead of %%LOCALAPPDATA%%/VoiceWriter")
parser.add_argument("--port", type=int, help="remote microphone port (default: remote_port in settings)")
args = parser.parse_args()
if args.data_dir:
    os.environ["VOICEWRITER_DATA"] = os.path.abspath(args.data_dir)  # before config is imported

if args.remote_only:
    from .remote import main as remote_main
    sys.exit(remote_main(args.port))

from .app import main  # noqa: E402

sys.exit(main())
