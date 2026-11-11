"""Offline hosted profile test with fictional evidence."""
from datetime import date
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class EvidenceProfile(unittest.TestCase):
    def test_profile_uses_eligible_evidence_instead_of_base_cvs(self):
        with tempfile.TemporaryDirectory(prefix="hq-profile-test-") as folder:
            root = Path(folder)
            scripts = root / "tools" / "hq-dashboard"
            scripts.mkdir(parents=True)
            # All settings and evidence are fictional.
            for name in ("profile_builder.py", "ai_tasks.py", "lead_policy.py", "lead_ranker.py",
                         "hq_settings.py", "docx_export.py", "cv_layout.py"):
                shutil.copyfile(HERE / name, scripts / name)
            shutil.copyfile(HERE.parents[1] / "settings.example.json", root / "settings.example.json")
            (root / "settings.json").write_text(
                json.dumps({"excluded_evidence_ids": ["E-013"]}), encoding="utf-8")
            rules = "Use only the fictional evidence below.\n"
            (root / "rules.md").write_text(rules, encoding="utf-8")
            evidence = root / "evidence"
            evidence.mkdir()
            for i, level, public, bullet, limit in (
                ("E-998", "verified", "yes, retain the fictional project name", "Built a fictional workflow.", "Prototype only."),
                ("E-997", "stated", "yes", "Directed fictional agents.", "Agents wrote the code."),
                ("E-996", "unverified", "yes", "Unverified fictional claim.", "Unverified."),
                ("E-995", "verified", "no", "Private fictional claim.", "Private."),
                ("E-013", "verified", "yes", "Excluded fictional claim.", "Excluded."),
                ("E-994", "verified", "yes", "Not CV-ready yet.", "No wording."),
            ):
                (evidence / f"{i}-fictional.md").write_text(
                    f"- level: {level}\n- public-safe: {public}\n- CV-ready bullet: {bullet}\n- limit: {limit}\n",
                    encoding="utf-8",
                )
            cv = root / "cv"
            cv.mkdir()
            for name in ("cv-v1-variant-a-business-consultant.md", "cv-v1-variant-b-ai-operations.md"):
                (cv / name).write_text("STALE BASE CV SENTINEL", encoding="utf-8")
            spec = importlib.util.spec_from_file_location("hq_profile_fixture", scripts / "profile_builder.py")
            seed = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(seed)
            first = seed.profile()
            bank = first["bank"]
            self.assertTrue(bank.startswith(rules + "\nEVIDENCE BANK (eligible entries only)\n"))
            self.assertIn("[E-997] (stated) Directed fictional agents. | Limits: Agents wrote the code. | Publication: yes", bank)
            self.assertIn("[E-998] (verified) Built a fictional workflow. | Limits: Prototype only. | Publication: yes, retain the fictional project name", bank)
            self.assertLess(bank.index("[E-997]"), bank.index("[E-998]"))
            for text in ("[E-996]", "[E-995]", "[E-013]", "[E-994]", "Unverified fictional claim.",
                         "Private fictional claim.", "Excluded fictional claim.", "Not CV-ready yet.", "STALE BASE CV SENTINEL", "BASE CV, VARIANT"):
                self.assertNotIn(text, bank)
            self.assertEqual(first["updated"], date.today().isoformat())
            self.assertEqual(first["source"], "evidence/ (eligible entries) + AGENTS.md rules + jobs/scoring-rubric.md")
            # A subsequent profile build must reread evidence, without seed/queue side effects.
            path = evidence / "E-997-fictional.md"
            path.write_text(path.read_text(encoding="utf-8").replace("Directed fictional agents.", "Directed updated fictional agents."), encoding="utf-8")
            self.assertIn("Directed updated fictional agents.", seed.profile()["bank"])
            self.assertFalse((root / "work").exists())
            self.assertEqual(list(root.rglob("*.sqlite3")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
