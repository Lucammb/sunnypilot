"""Runs the lightpilot tests without the compiled parts (params, msgq). Run from the repo root:
  python openpilot/lightpilot/tests/run_tests.py openpilot/lightpilot/tests/ -k "not end_to_end"
(the end-to-end test needs the PHP server files and php, which are not in the fork: leave it out). Used by the weekly upstream update."""
import os, sys
sys.path[:0] = [os.getcwd(), os.path.join(os.getcwd(), "opendbc_repo")]
import types
m = types.ModuleType("openpilot.common.params_pyx")
class P:
  def __init__(self,*a,**k): pass
m.Params=P; m.ParamKeyType=object; m.UnknownKeyName=Exception
sys.modules["openpilot.common.params_pyx"]=m
pm_ = types.ModuleType("openpilot.common.params")
pm_.Params=P; pm_.ParamKeyType=object; pm_.UnknownKeyName=Exception
sys.modules["openpilot.common.params"]=pm_
# messaging stub (msgq is compiled, not available here): only new_message is needed
from openpilot.cereal import log
msg = types.ModuleType("openpilot.cereal.messaging")
def new_message(service=None, size=None, **kwargs):
  dat = log.Event.new_message(**kwargs)
  if service:
    dat.init(service)
  return dat
msg.new_message = new_message
msg.PubMaster = msg.SubMaster = object
sys.modules["openpilot.cereal.messaging"] = msg
import openpilot.cereal as _c; _c.messaging = msg
from unittest.mock import MagicMock
for n in ('msgq','msgq.visionipc'): sys.modules[n]=MagicMock()
import pytest
sys.exit(pytest.main(["-c","/dev/null","--rootdir=.","-q","-p","no:cacheprovider","-x"]+sys.argv[1:]))
