import json
import os
import sys
import tempfile
import hashlib
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from recovery import bounded, wait_healthy, telemetry_only, ControllerState, pid_alive


def failed(key='a'):
    return {'key': key, 'status': 'invalid', 'attempt_id': str(time.time_ns()),
            'telemetry_limitations': ['gap'], 'spill_observed': False,
            'summary': {'errors_total': 0}}


class RecoveryTests(unittest.TestCase):
    def test_valid_never_repeated(self):
        run = Mock()
        bounded({'key':'a'}, [{'key':'a','status':'complete'}], run, Mock(), Mock())
        run.assert_not_called()

    def test_single_retry_then_success(self):
        run = Mock(side_effect=[failed(), {'key':'a','status':'complete','attempt_id':'ok'}])
        health = Mock(); incident = Mock()
        result = bounded({'key':'a'}, [], run, health, incident)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(run.call_count, 2); self.assertEqual(health.call_count, 1)

    def test_second_failure_pending_then_next_scenario(self):
        bad1, bad2 = failed(), failed()
        health = Mock(); incident = Mock(); run = Mock(side_effect=[bad1,bad2])
        self.assertIsNone(bounded({'key':'a'}, [], run, health, incident))
        self.assertEqual(run.call_count, 2); self.assertEqual(health.call_count, 2)
        self.assertEqual(incident.call_args.args[1], 'pending_incident')
        next_run = Mock(return_value={'key':'b','status':'complete','attempt_id':'next'})
        self.assertEqual(bounded({'key':'b'}, [bad1,bad2], next_run, health, incident)['status'],'complete')

    def test_restart_does_not_reset_budget(self):
        run = Mock(); health = Mock()
        bounded({'key':'a'}, [failed(),failed()], run, health, Mock())
        run.assert_not_called(); health.assert_called_once()
        run = Mock(return_value={'key':'a','status':'complete','attempt_id':'ok'})
        bounded({'key':'a'}, [failed()], run, Mock(), Mock())
        run.assert_called_once()

    def test_persistent_unhealthy_stops_without_retry(self):
        health=Mock(side_effect=RuntimeError('blocked')); run=Mock(return_value=failed())
        with self.assertRaisesRegex(RuntimeError,'blocked'):
            bounded({'key':'a'}, [], run, health, Mock())
        run.assert_called_once()

    def test_nontelemetry_failure_not_retried(self):
        for extra in ({'spill_observed':None},{'audit_error':'bad'},{'oom_killed':True},
                      {'incomplete':True},{'summary':{'errors_total':1}}):
            bad=dict(failed(),**extra);self.assertFalse(telemetry_only(bad))
            run=Mock(return_value=bad)
            with self.assertRaises(RuntimeError):bounded({'key':'a'},[],run,Mock(),Mock())
            run.assert_called_once()

    def test_verified_pg_timeout_with_missing_telemetry_can_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'worker-00.jsonl'
            timeout={'ok':False,'error':{'type':'DatabaseError','message':str({
                'C':'57014','M':'canceling statement due to statement timeout'})}}
            path.write_text(json.dumps(timeout)+'\n')
            result=dict(failed(), raw=folder, cell={'engine':'postgresql','concurrency':1},
                        summary={'errors_total':1})
            self.assertTrue(telemetry_only(result))
            run=Mock(return_value={'key':'a','status':'complete','attempt_id':'retry'})
            health=Mock()
            bounded({'key':'a'},[result],run,health,Mock())
            run.assert_called_once();health.assert_called_once()
            # Ni error de conexión, ni cancelación manual, ni conteo inconsistente se reclasifican.
            for detail in ({'C':'08006','M':'connection failure'},
                           {'C':'57014','M':'canceling statement due to user request'}):
                timeout['error']['message']=str(detail);path.write_text(json.dumps(timeout)+'\n')
                self.assertFalse(telemetry_only(result))
            path.write_text('')
            self.assertFalse(telemetry_only(result))

    @patch('recovery.policy', return_value={'load_cutoff_epoch':float('inf')})
    def test_health_wait_is_bounded_and_honors_stop(self, policy):
        ticks=[0]; probes=[]
        def sleep(n):ticks[0]+=n
        probe=Mock(side_effect=RuntimeError('down'))
        with self.assertRaisesRegex(RuntimeError,'180s'):
            wait_healthy('id',probes.append,lambda:False,clock=lambda:ticks[0],sleep=sleep,probe=probe,timeout=20)
        self.assertEqual(ticks[0],20);self.assertEqual(probe.call_count,3)
        with self.assertRaisesRegex(RuntimeError,'pausa'):
            wait_healthy('id',probes.append,lambda:True,probe=probe)

    @patch('recovery.policy', return_value={'load_cutoff_epoch':0})
    def test_deadline_prevents_recovery(self, policy):
        probe=Mock()
        with self.assertRaises(RuntimeError):wait_healthy('id',Mock(),lambda:False,probe=probe)
        probe.assert_not_called()

    def test_state_heartbeat_and_terminal_status(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'state.json'
            def save(path,data):path.write_text(json.dumps(data))
            state=ControllerState(p,save);state.update(phase='recovering_telemetry')
            state.close('blocked',error='test')
            result=json.loads(p.read_text())
            self.assertEqual(result['phase'],'blocked');self.assertEqual(result['pid'],os.getpid())
            self.assertTrue(pid_alive(os.getpid()))

    def test_real_matrix_flow_two_failures_then_next_without_new_controller(self):
        import orchestrate as o
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root=Path(folder);(root/'config').mkdir();(root/'config/images.lock.json').write_bytes(b'lock')
            (root/'config/acceptance.json').write_text(json.dumps({'pilot_passed':True,'source_sha256':'frozen',
                'image_lock_sha256':hashlib.sha256(b'lock').hexdigest()}))
            guard=root/'evidence/deadline/20261101';guard.mkdir(parents=True)
            (guard/'current.json').write_text(json.dumps({'directory':str(guard)}))
            (guard/'process.json').write_text(json.dumps({'project':'test','hard_epoch':99999999999}))
            (guard/'heartbeat.json').write_text(json.dumps({'epoch':time.time()}))
            cells=[{'key':k,'engine':'postgresql','profile':'PROFILE_B_MEDIUM','query_id':'Q1',
                    'variant':'default','concurrency':1,'run_number':1} for k in ['a','b']]
            calls=[]
            def execute(cell,*args):
                calls.append(cell['key']);n=len(calls)
                result=dict(failed(cell['key']),attempt_id='attempt'+str(n),mode='final',cell=cell,container_id='id')
                if cell['key']=='b':result['status']='complete'
                path=root/'results/final'/result['attempt_id'];path.mkdir()
                (path/'manifest.json').write_text(json.dumps(result));return result
            stack.enter_context(patch.object(o,'ROOT',root))
            stack.enter_context(patch.object(o,'source_hash',return_value='frozen'))
            stack.enter_context(patch.object(o,'delivery_schedule',return_value=cells))
            stack.enter_context(patch.object(o,'admission',return_value={'allowed':True,'hard_epoch':99999999999}))
            stack.enter_context(patch.object(o,'compose',return_value='id'))
            stack.enter_context(patch.object(o,'execute',side_effect=execute))
            stack.enter_context(patch.object(o,'progress_update'))
            health=stack.enter_context(patch.object(o,'wait_healthy'))
            stack.enter_context(patch.object(o,'call',return_value=''))
            stack.enter_context(patch('audit_run.audit',return_value={'passed':True}))
            stack.enter_context(patch.object(sys,'argv',['orchestrate.py','matrix','--mode','final','--project','test','--allow-kernel-flush']))
            try:o.main()
            finally:os.chdir(original)
            self.assertEqual(calls,['a','a','b']);self.assertEqual(health.call_count,2)
            self.assertFalse((root/'results/final/controller.lock').exists())
            state=json.loads((root/'results/final/controller-state.json').read_text())
            self.assertEqual(state['phase'],'finished')
            self.assertEqual(json.loads((root/'results/final/incidents/a.json').read_text())['state'],'pending_incident')


if __name__ == '__main__':unittest.main()
