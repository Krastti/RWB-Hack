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

    def setUp(self):
        self.plan = load(ROOT / "examples/generation-plan.json")
        self.profile = load(ROOT.parent.parent / "templates/profiles/web-fastapi-angular/profile.json")

    def test_file_must_be_inside_granted_directory(self):
        task = self.plan["tasks"][0]
        task["output_directories"] = []
        task["output_paths"] = ["backend/app/api"]
        with self.assertRaisesRegex(ContractError, "exceeds profile grants"):
            validate_profile_grants(self.plan, self.profile)
        task["output_paths"] = ["backend/app/api/routes.py"]
        validate_profile_grants(self.plan, self.profile)

    def test_foreign_path_and_owner_and_profile_are_rejected(self):
        for path in ("backend/migrations/001.py", "backend/app/integrations/client.py"):
            with self.subTest(path=path):
                self.plan["tasks"][0]["output_paths"] = [path]
                with self.assertRaisesRegex(ContractError, "exceeds profile grants"):
                    validate_profile_grants(self.plan, self.profile)
        self.setUp()
        self.plan["tasks"][0].update(owner_agent="unknown", output_paths=[], output_directories=[])
        with self.assertRaisesRegex(ContractError, "Unknown profile owner"):
            validate_profile_grants(self.plan, self.profile)
        self.setUp()
        self.plan["profile_id"] = "web-fastapi-react"
        with self.assertRaisesRegex(ContractError, "Profile identity mismatch"):
            validate_profile_grants(self.plan, self.profile)

    def test_file_directory_collision_within_task_is_rejected(self):
        task = self.plan["tasks"][0]
        for directories, paths in ((["backend/app/api"], ["backend/app/api"]),
                                   (["backend/app/api/sub"], ["backend/app/api"]),
                                   ([], ["backend/app/api/file", "backend/app/api/file/child"])):
            task.update(output_paths=paths, output_directories=directories)
            for validate in (lambda: Contracts().validate("generation-plan", self.plan),
                             lambda: validate_profile_grants(self.plan, self.profile)):
                with self.assertRaisesRegex(ContractError, "Conflicting output file and directory"):
                    validate()


class ArtifactIntegrityTests(unittest.TestCase):
    def test_corrupt_fixture_size_or_checksum_is_rejected(self):
        for field, value in (("size_bytes", 0), ("checksum", "0" * 64)):
            artifact = load(ROOT / "examples/agent-result.architecture.json")["output_artifacts"][0]
            artifact[field] = value
            with self.assertRaises(ContractError):
                Contracts().validate("artifact", artifact)


if __name__ == "__main__":
    unittest.main()
