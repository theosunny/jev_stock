import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'stock-signal/scripts'))
import push

class FeishuOnceTests(unittest.TestCase):
    def test_success_preserves_message_receipt_and_cron_path(self):
        response=subprocess.CompletedProcess([],0,json.dumps({'ok':True,'data':{'message_id':'om_test'}}),'')
        with patch.dict(push.ENV, {'LARK_USER_OPEN_ID':'test_user'}, clear=True), patch.object(push,'_load_env'), patch.object(push,'_lark_bin',return_value='/custom/bin/lark-cli'), patch.object(push.subprocess,'run',return_value=response) as run:
            self.assertEqual(push.send_feishu_once('report'),(True,'om_test'))
            self.assertTrue(run.call_args.kwargs['env']['PATH'].startswith('/custom/bin:'))
            self.assertEqual(run.call_count,1)
    def test_unknown_result_never_falls_back_or_retries(self):
        for response in (subprocess.TimeoutExpired('lark',45),subprocess.CompletedProcess([],1,'not-json','failed')):
            with patch.dict(push.ENV,{'LARK_USER_OPEN_ID':'test_user'},clear=True),patch.object(push,'_load_env'),patch.object(push.subprocess,'run') as run:
                if isinstance(response,Exception):run.side_effect=response
                else:run.return_value=response
                self.assertFalse(push.send_feishu_once('report')[0])
                self.assertEqual(run.call_count,1)
    def test_unconfigured_does_not_send(self):
        with patch.dict(push.ENV,{},clear=True),patch.object(push,'_load_env'),patch.object(push.subprocess,'run') as run:
            self.assertIsNone(push.send_feishu_once('report'))
            run.assert_not_called()
