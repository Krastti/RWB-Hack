"""Regression tests for generation task identity."""

import unittest

from validate import ContractError, Contracts, ROOT, load, validate_profile_grants


class GenerationPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contracts = Contracts()

    def setUp(self):
        self.plan = load(ROOT / "examples" / "generation-plan.json")
        auth, orders = self.plan["tasks"]
        auth.update(task_id="implement-auth", stage_id="backend-auth",
                    owner_agent="backend", output_directories=["backend/auth"])
        orders.update(task_id="implement-orders", stage_id="backend-orders",
                      owner_agent="backend", output_directories=["backend/orders"],
                      depends_on=["implement-auth"])

    def test_distinct_stages_can_share_an_agent(self):
        self.contracts.validate("generation-plan", self.plan)

    def test_distinct_tasks_cannot_share_a_stage(self):
        # The plan is otherwise valid: only the execution node identity collides.
        self.contracts.validate("generation-plan", self.plan)
        self.plan["tasks"][1]["stage_id"] = self.plan["tasks"][0]["stage_id"]

        with self.assertRaisesRegex(ContractError, "^Duplicate generation stage ID$"):
            self.contracts.validate("generation-plan", self.plan)


class ProfileGrantsTests(unittest.TestCase):
    def test_example_and_parent_directory_denial(self):
        plan = load(ROOT / "examples/generation-plan.json")
        profile = load(ROOT.parent.parent / "templates/profiles/web-fastapi-angular/profile.json")
        validate_profile_grants(plan, profile)
        plan["tasks"][0]["output_directories"] = ["backend"]
        with self.assertRaisesRegex(ContractError, "exceeds profile grants"):
            validate_profile_grants(plan, profile)


if __name__ == "__main__":
    unittest.main()
