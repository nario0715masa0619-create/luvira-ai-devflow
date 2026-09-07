import unittest
from unittest.mock import Mock
from v3_transaction import V3Transaction
from execution_platform import TaskSpec, V3Task, ExecutionRecord, V3Status
class V3TransactionTest(unittest.TestCase):
 def test_requires_one_transaction_for_both_documents(self):
  c=Mock(); c.collection.return_value.document.return_value=Mock(); c.transactional=lambda f:f
  tx=Mock(); tx.get.return_value=Mock(exists=False); c.transaction.return_value=tx
  spec=TaskSpec.from_dict({"repository":"a/b","base_commit":"a"*40,"requested_action":"implementation","acceptance_criteria":["x"],"budget":{"max_cost_usd":1},"expiry":"2026-12-01T00:00:00Z","execution_scope":{"allowed_paths":["src/"]},"model_policy":"low-cost"})
  task=V3Task("t",spec,V3Status.AUTHORIZED); record=ExecutionRecord("e","t",spec.hash,1,V3Status.EXECUTION_QUEUED)
  V3Transaction(c).create_queued(task,record)
  self.assertEqual(tx.create.call_count,2)
