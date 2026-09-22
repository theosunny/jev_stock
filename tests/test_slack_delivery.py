"""Bot delivery integrates with the same durable per-channel outbox."""
import datetime as dt
import os
import unittest
from unittest.mock import patch
import test_delivery
import codex_cycle as cycle


class SlackDeliveryTests(unittest.TestCase):
    setUp = test_delivery.DeliveryTests.setUp
    tearDown = test_delivery.DeliveryTests.tearDown
    run_at = test_delivery.DeliveryTests.run_at

    def test_bot_success_stores_target_and_ack(self):
        import slack_bot
        report = self.run_at()
        with patch.object(slack_bot, 'open_dm', return_value='DNEW'), patch.object(slack_bot, 'send_once', return_value={'ok': True, 'channel': 'DNEW', 'ts': '1790060000.123456'}) as sender:
            result = cycle.send_slack(self.root, report['report_id'], self.now)
        self.assertEqual(result['pending_channels'], ['feishu'])
        record = cycle.read_record(self.root, report['report_id'])
        self.assertEqual(record['slack_target'], 'DNEW')
        self.assertEqual(record['slack_transport'], 'bot')
        sender.assert_called_once_with(record['message'], channel='DNEW')

    def test_uncertain_send_requires_bot_readback_and_no_resend(self):
        import slack_bot
        report = self.run_at()
        with patch.object(slack_bot, 'open_dm', return_value='DNEW'), patch.object(slack_bot, 'send_once', return_value={'ok': False, 'error': 'timeout'}) as sender:
            self.assertEqual(cycle.send_slack(self.root, report['report_id'], self.now)['status'], 'delivery_uncertain')
            self.assertEqual(cycle.send_slack(self.root, report['report_id'], self.now)['status'], 'needs_verification')
            sender.assert_called_once()
        with patch.object(slack_bot, 'verify', return_value={'status': 'verified', 'receipt': '1790060000.123456'}) as verifier:
            result = cycle.verify_slack(self.root, report['report_id'], self.now)
        self.assertEqual(result['pending_channels'], ['feishu'])
        self.assertEqual(verifier.call_args.args[1], 'DNEW')

    def test_absence_does_not_automatically_release_and_legacy_needs_connector(self):
        import slack_bot
        report = self.run_at()
        cycle.claim(self.root, report['report_id'], 'slack', self.now)
        with patch.object(slack_bot, 'verify') as verifier:
            self.assertEqual(cycle.verify_slack(self.root, report['report_id'], self.now)['status'], 'legacy_connector_verification_required')
            verifier.assert_not_called()
        record = cycle.read_record(self.root, report['report_id'])
        cycle.write(cycle.record_path(self.root, report['report_id']), {**record, 'slack_transport': 'bot', 'slack_target': 'DNEW'})
        with patch.object(slack_bot, 'verify', return_value={'status': 'absent'}):
            self.assertEqual(cycle.verify_slack(self.root, report['report_id'], self.now)['status'], 'verified_absent')
        self.assertEqual(cycle.pending(self.root,self.now)[0]['status'], 'needs_verification')

    def test_dry_run_and_expired_report_never_contact_slack(self):
        import slack_bot
        report = self.run_at()
        with patch.object(slack_bot, 'open_dm') as opener, patch.dict(os.environ, {'NO_PUSH': '1'}):
            self.assertEqual(cycle.send_slack(self.root,report['report_id'],self.now)['status'], 'dry_run')
            opener.assert_not_called()
        with patch.object(slack_bot, 'open_dm') as opener:
            self.assertEqual(cycle.send_slack(self.root,report['report_id'],self.now+dt.timedelta(days=1))['status'], 'skipped')
            opener.assert_not_called()
