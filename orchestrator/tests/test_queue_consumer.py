import unittest
from execution_platform import ExecutionPlatform, TaskSpec, V3Status
from execution_preflight import ExecutionPreflight, PreflightCheck, REQUIRED_CHECKS
from execution_queue import ExecutionQueue
from execution_record_store import InMemoryExecutionRecordStore
from queue_consumer import QueueConsumer

def make():
 p=ExecutionPlatform(); t=p.create(TaskSpec.from_dict({"repository":"a/b","base_commit":"a"*40,"requested_action":"implementation","acceptance_criteria":["x"],"budget":{"max_cost_usd":1},"expiry":"2026-12-01T00:00:00Z","execution_scope":{"allowed_paths":["src/"]},"model_policy":"low-cost"}),"t");p.validate("t"); w=p.request_approval("t");p.authorize("t",w.approval_binding);s=InMemoryExecutionRecordStore();q=ExecutionQueue(p,ExecutionPreflight([lambda _,n=n:PreflightCheck(n,True,"OK") for n in REQUIRED_CHECKS]),s);return p,s,q.request("t")[0]
class C:
 def launch(self,r): return "operations/x"
class QueueConsumerTest(unittest.TestCase):
 def test_only_persisted_queued_record_can_launch(self):
  p,s,r=make(); out=QueueConsumer(p,s,C()).consume("t",r.execution_id);self.assertEqual(out.external_operation_id,"operations/x");self.assertEqual(p.get("t").status,V3Status.EXECUTION_RUNNING)
 def test_duplicate_consume_is_rejected(self):
  p,s,r=make(); c=QueueConsumer(p,s,C());c.consume("t",r.execution_id)
  with self.assertRaisesRegex(ValueError,"queued_execution_identity_invalid"):c.consume("t",r.execution_id)
