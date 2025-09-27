import pathlib
import sys
import types
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if 'boto3' not in sys.modules:
    fake_boto3 = types.SimpleNamespace(client=lambda *args, **kwargs: MagicMock())
    sys.modules['boto3'] = fake_boto3

if 'botocore' not in sys.modules:
    exceptions_module = types.SimpleNamespace(BotoCoreError=Exception, ClientError=Exception)
    botocore_module = types.SimpleNamespace(exceptions=exceptions_module)
    sys.modules['botocore'] = botocore_module
    sys.modules['botocore.exceptions'] = exceptions_module
