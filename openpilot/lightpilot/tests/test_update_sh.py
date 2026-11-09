"""The boot-time updater and its background downloader (lightpilot_update.sh), against a local fake remote."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "lightpilot_update.sh"


def git(cwd, *args):
  return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Rig:
  def __init__(self, tmp_path):
    self.remote = tmp_path / "remote"
    self.remote.mkdir()
    git(self.remote, "init", "-q", "-b", "lightpilot")
    git(self.remote, "config", "user.email", "t@t")
    git(self.remote, "config", "user.name", "t")
    git(self.remote, "config", "uploadpack.allowAnySHA1InWant", "true")
    self.c1 = self.commit("a")
    self.work = tmp_path / "work"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{self.remote}", str(self.work))
    self.c2 = self.commit("b")
    self.dir = tmp_path / "lp"
    self.stable = tmp_path / "STABLE"  # missing = offline

  def commit(self, text):
    (self.remote / "f").write_text(text)
    git(self.remote, "add", "f")
    git(self.remote, "commit", "-q", "-m", text)
    return git(self.remote, "rev-parse", "HEAD")

  def run(self, *args, timeout=30):
    env = dict(os.environ, LP_REPO=f"file://{self.remote}", LP_STABLE_URL=f"file://{self.stable}", LP_WORK=str(self.work),
               LP_LOG=str(self.dir / "update.log"), LP_NO_LOOP="1", LP_FAST="0.2", LP_SLOW="100")
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=timeout)

  def loop_for(self, seconds):
    try:
      self.run("--loop", timeout=seconds)
    except subprocess.TimeoutExpired:
      pass

  def head(self):
    return git(self.work, "rev-parse", "HEAD")

  def log(self):
    return (self.dir / "update.log").read_text()


def test_offline_at_boot_stays_put(tmp_path):
  rig = Rig(tmp_path)
  rig.run()
  assert rig.head() == rig.c1
  assert "offline at boot" in rig.log()


def test_online_at_boot_updates(tmp_path):
  rig = Rig(tmp_path)
  rig.stable.write_text(rig.c2 + "\n")
  rig.run()
  assert rig.head() == rig.c2
  assert (rig.dir / "previous_sha").read_text().strip() == rig.c1


def test_download_while_driving_then_apply_at_next_boot_offline(tmp_path):
  rig = Rig(tmp_path)
  rig.run()  # boot with no network
  rig.stable.write_text(rig.c2 + "\n")  # the car is on, the wifi comes up
  rig.loop_for(4)
  assert (rig.dir / "pending_sha").read_text().strip() == rig.c2
  assert rig.head() == rig.c1  # downloaded, not applied while driving
  rig.stable.unlink()  # next start: still no network yet
  rig.run()
  assert rig.head() == rig.c2
  assert not (rig.dir / "pending_sha").exists()


def test_garbage_stable_is_ignored(tmp_path):
  rig = Rig(tmp_path)
  rig.stable.write_text("<html>not a sha</html>")
  rig.run()
  assert rig.head() == rig.c1


def test_already_on_stable_does_nothing(tmp_path):
  rig = Rig(tmp_path)
  rig.stable.write_text(rig.c1 + "\n")
  rig.run()
  assert rig.head() == rig.c1
  rig.loop_for(2)
  assert not (rig.dir / "pending_sha").exists()
