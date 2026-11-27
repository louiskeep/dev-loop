"""A tiny real git repo with a bare origin, for tests that ask git questions."""

import subprocess
from pathlib import Path

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def git(path, *args):
    r = subprocess.run(
        [*GIT, "-C", str(path), *args], check=True, capture_output=True, text=True
    )
    return r.stdout.strip()


class Fix:
    def __init__(self, root: Path):
        self.origin = root / "origin.git"
        self.path = root / "work"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True
        )
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.path)], check=True
        )
        git(self.path, "checkout", "-q", "-b", "main")
        self.write("shared.txt", "a\n" + "x\n" * 10 + "z\n")
        self.write("base.txt", "b")
        self.commit("base")
        git(self.path, "push", "-q", "origin", "main")

    def write(self, name, text):
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg):
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", msg)
        return self.sha()

    def sha(self, ref="HEAD"):
        return git(self.path, "rev-parse", ref)

    def branch(self, name, frm="main"):
        git(self.path, "checkout", "-q", "-B", name, frm)

    def checkout(self, name):
        git(self.path, "checkout", "-q", name)

    def advance_main(self, name, text="m"):
        """A new commit on main, pushed to origin; returns to the current branch."""
        cur = git(self.path, "rev-parse", "--abbrev-ref", "HEAD")
        self.checkout("main")
        self.write(name, text)
        sha = self.commit(f"main {name}")
        git(self.path, "push", "-q", "origin", "main")
        self.checkout(cur)
        return sha

    def merge_main(self):
        git(self.path, "merge", "-q", "--no-edit", "main")
        return self.sha()

    def edit_top(self, name="shared.txt"):
        """Change the first line of a file (merges cleanly with a bottom edit)."""
        self.write(name, "A\n" + "x\n" * 10 + "z\n")

    def advance_main_bottom(self, name="shared.txt"):
        cur = git(self.path, "rev-parse", "--abbrev-ref", "HEAD")
        self.checkout("main")
        self.write(name, "a\n" + "x\n" * 10 + "Z\n")
        sha = self.commit("main edits the bottom")
        git(self.path, "push", "-q", "origin", "main")
        self.checkout(cur)
        return sha
