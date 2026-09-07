"""Atomic v3 task/execution persistence boundary."""
from typing import Any
from v3_task_store import task_payload
from execution_record_store import record_payload
from execution_platform import V3Task, ExecutionRecord

class V3Transaction:
 def __init__(self, client: Any, task_collection="devflow_v3_tasks", record_collection="devflow_execution_records"):
  self.client=client; self.tasks=client.collection(task_collection); self.records=client.collection(record_collection)
 def create_queued(self, task: V3Task, record: ExecutionRecord):
  task_ref=self.tasks.document(task.task_id); record_ref=self.records.document(record.execution_id)
  @self.client.transactional
  def write(tx):
   if tx.get(task_ref).exists or tx.get(record_ref).exists: raise ValueError("v3_identity_exists")
   tx.create(task_ref, task_payload(task)); tx.create(record_ref, record_payload(record))
  write(self.client.transaction())
