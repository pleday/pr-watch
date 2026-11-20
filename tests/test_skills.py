import re
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / '.claude' / 'skills'


class SkillsTest(unittest.TestCase):
    """The skills shipped with the project, read by Claude Code in the workspaces that add it: their header must be valid."""

    def test_every_skill_has_a_name_that_is_its_folder_and_a_description(self):
        files = sorted(SKILLS.glob('*/SKILL.md'))
        self.assertGreaterEqual(len(files), 4)
        for file in files:
            header = re.match(r'---\nname: ([\w-]+)\ndescription: (.+)\n---\n', file.read_text(encoding='utf-8'))
            self.assertIsNotNone(header, f'{file}: header')
            self.assertEqual(header.group(1), file.parent.name)
            self.assertGreater(len(header.group(2)), 60, f'{file}: the description is what makes Claude pick the skill')

    def test_the_commands_the_skills_name_exist(self):
        from prwatch import cli
        source = Path(cli.__file__).read_text(encoding='utf-8')
        for command in ('register', 'doctor', 'ensure', 'stop', 'ui', 'list', 'once', 'settings', 'unregister', 'tray', 'install-desktop', 'install-launcher'):
            self.assertRegex(source, r"['\"]" + re.escape(command) + r"['\"]", command)


if __name__ == '__main__':
    unittest.main()
