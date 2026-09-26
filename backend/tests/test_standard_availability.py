import unittest
from datetime import date

from app.services.standard_availability import evaluate_standard_availability
from app.services.task import TaskService
from app.store import store


class StandardAvailabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 9, 26)
        self.original_standard_rows = [dict(row) for row in store.rows("standard")]
        self.original_task_rows = [dict(row) for row in store.rows("task")]
        store.rows("standard").clear()
        store.rows("task").clear()

    def tearDown(self) -> None:
        store.rows("standard").clear()
        store.rows("standard").extend(self.original_standard_rows)
        store.rows("task").clear()
        store.rows("task").extend(self.original_task_rows)

    def make_standard(self, **overrides):
        row = {
            "id": len(store.rows("standard")) + 1,
            "标物编号": f"STAN-{len(store.rows('standard')) + 1:04d}",
            "标物名称": "标准物质",
            "有效期至": "2026-12-31",
            "标物状态": "合格在用",
            "存放条件": "2-8℃冷藏",
            "status": "合格在用",
        }
        row.update(overrides)
        store.rows("standard").append(row)
        return row

    def test_expired_consumed_and_invalid_storage_are_unavailable(self):
        expired = evaluate_standard_availability(
            self.make_standard(标物编号="EXP", 有效期至="2026-09-25"),
            today=self.today,
        )
        consumed = evaluate_standard_availability(
            self.make_standard(标物编号="USED", 标物状态="已消耗", status="已消耗"),
            today=self.today,
        )
        bad_storage = evaluate_standard_availability(
            self.make_standard(标物编号="BAD", 存放条件="冷藏条件异常"),
            today=self.today,
        )

        self.assertFalse(expired.available)
        self.assertFalse(consumed.available)
        self.assertFalse(bad_storage.available)
        self.assertEqual(expired.availability, "不可用")
        self.assertIn("已过期", expired.reason)
        self.assertIn("标物已消耗", consumed.reasons)
        self.assertIn("不符合要求", bad_storage.reason)

    def test_expiring_status_is_still_available_with_warning(self):
        row = self.make_standard(标物编号="WARN", 有效期至="2026-10-10", 标物状态="即将到期")
        availability = evaluate_standard_availability(row, today=self.today)

        self.assertTrue(availability.available)
        self.assertEqual(availability.availability, "有条件可用")
        self.assertTrue(availability.warnings)

    def test_availability_is_attached_without_mutating_ledger_record(self):
        row = self.make_standard(标物编号="IMMUTABLE")
        original = dict(row)

        annotated = evaluate_standard_availability(row, today=self.today).to_dict()

        self.assertIn("available", annotated)
        self.assertNotIn("availability", row)
        self.assertEqual(original, row)

    def test_storage_text_with_negative_word_but_explicitly_normal_is_available(self):
        row = self.make_standard(标物编号="NORMAL-STORAGE", 存放条件="常温避光，无异常")
        availability = evaluate_standard_availability(row, today=self.today)

        self.assertTrue(availability.available)
        self.assertEqual(availability.availability, "可用")

    def test_consumed_history_wins_even_when_display_status_is_active(self):
        row = self.make_standard(标物编号="HISTORY", 标物状态="合格在用", status="已消耗")
        availability = evaluate_standard_availability(row, today=self.today)

        self.assertFalse(availability.available)
        self.assertEqual(availability.lifecycle_status, "已消耗")

    def test_ledger_detail_and_task_share_one_conclusion(self):
        row = self.make_standard(
            标物编号="SHARED",
            有效期至="2026-09-25",
            标物状态="合格在用",
            存放条件="常温",
        )
        task = {
            "id": 1,
            "任务编号": "TASK-0001",
            "关联样品": "样品",
            "检测项目": "项目",
            "引用标物编号": "SHARED",
        }
        store.rows("task").append(task)

        from app.services.standard import StandardService

        ledger, _total = StandardService().list_entries(today=self.today)
        detail = StandardService().get_entry(row["id"], today=self.today)
        annotated_task = TaskService().get_entry(1, today=self.today)

        self.assertFalse(ledger[0]["availability"]["available"])
        self.assertEqual(
            ledger[0]["availability"]["reason"],
            detail["availability"]["reason"],
        )
        self.assertEqual(
            ledger[0]["availability"]["reason"],
            annotated_task["referenceAvailability"]["reason"],
        )

    def test_task_creation_rejects_unavailable_reference_without_changing_history(self):
        self.make_standard(标物编号="EXP", 有效期至="2026-09-25")
        before = [dict(row) for row in store.rows("task")]

        entry, missing, error = TaskService().create_entry(
            {
                "任务编号": "TASK-X",
                "关联样品": "样品",
                "检测项目": "项目",
                "引用标物编号": "EXP",
            },
            today=self.today,
        )

        self.assertIsNone(entry)
        self.assertEqual(missing, [])
        self.assertIn("引用标物 EXP 不可用", error)
        self.assertEqual(before, store.rows("task"))

    def test_task_without_reference_keeps_existing_action_flow(self):
        task = {"id": 1, "任务编号": "TASK-0001", "关联样品": "样品", "检测项目": "项目", "status": "待分配"}
        store.rows("task").append(task)

        entry, message = TaskService().run_action(1, "开始检测", today=self.today)

        self.assertEqual(message, "检测任务已开始检测")
        self.assertEqual(entry["status"], "检测中")
        self.assertNotIn("referenceAvailability", entry)

    def test_standard_number_is_only_a_reference_when_it_matches_standard_ledger(self):
        self.make_standard(标物编号="STAN-MATCH", 有效期至="2026-09-25")
        method_task = {
            "id": 1,
            "任务编号": "TASK-0001",
            "关联样品": "样品",
            "检测项目": "项目",
            "标准编号": "GB/T 1.1",
            "status": "待分配",
        }
        standard_task = dict(method_task, 标准编号="STAN-MATCH")
        store.rows("task").extend([method_task, standard_task])
        store.rows("task")[1]["id"] = 2

        method_entry, _message = TaskService().run_action(1, "开始检测", today=self.today)
        expired_entry, error = TaskService().run_action(2, "开始检测", today=self.today)

        self.assertEqual(method_entry["status"], "检测中")
        self.assertNotIn("referenceAvailability", method_entry)
        self.assertIsNone(expired_entry)
        self.assertIn("引用标物 STAN-MATCH 不可用", error)

    def test_task_action_rejects_unavailable_new_reference_without_mutating_task(self):
        self.make_standard(标物编号="EXP", 有效期至="2026-09-25")
        task = {"id": 1, "任务编号": "TASK-0001", "关联样品": "样品", "检测项目": "项目", "status": "待分配"}
        store.rows("task").append(task)
        before = dict(task)

        entry, error = TaskService().run_action(
            1,
            "开始检测",
            {"引用标物编号": "EXP"},
            today=self.today,
        )

        self.assertIsNone(entry)
        self.assertIn("引用标物 EXP 不可用", error)
        self.assertEqual(before, store.rows("task")[0])

    def test_task_creation_persists_reference(self):
        self.make_standard(标物编号="OK")
        entry, missing, error = TaskService().create_entry(
            {
                "任务编号": "TASK-OK",
                "关联样品": "样品",
                "检测项目": "项目",
                "引用标物编号": "OK",
                "检测地点": "一号实验室",
            },
            today=self.today,
        )

        self.assertIsNone(error)
        self.assertEqual(missing, [])
        self.assertEqual(entry["引用标物编号"], "OK")
        self.assertNotIn("检测地点", entry)
        self.assertTrue(entry["referenceAvailability"]["available"])


if __name__ == "__main__":
    unittest.main()
