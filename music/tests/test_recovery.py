import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.checkpoint import Checkpoint
from core.playlist import PlaylistError, create, register


class FakePlaylist:
    """Keep remote contents across local app restarts, including interrupted writes."""

    def __init__(self, playlist_id):
        self.playlist_id = playlist_id
        self.contents = []
        self.add_requests = []
        self.interrupt_next_write = False

    def get_playlist(self, playlistId, limit=None):
        if playlistId != self.playlist_id or limit is not None:
            raise AssertionError("Recovery must inspect the complete target playlist")
        return {"tracks": [{"videoId": video} for video in self.contents]}

    def add_playlist_items(self, playlistId, videoIds, duplicates=False):
        if playlistId != self.playlist_id:
            raise AssertionError("Unexpected destination playlist")
        self.add_requests.append(list(videoIds))
        if set(videoIds).intersection(self.contents) or len(videoIds) != len(set(videoIds)):
            raise AssertionError("The app attempted to send a duplicate ID")
        if self.interrupt_next_write:
            self.interrupt_next_write = False
            self.contents.append(videoIds[0])
            raise KeyboardInterrupt
        self.contents.extend(videoIds)
        return {
            "status": "STATUS_SUCCEEDED",
            "playlistEditResults": [
                {"videoId": video, "setVideoId": "set-" + video} for video in videoIds
            ],
        }


class RegistrationRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "작업.sqlite"
        self.store = Checkpoint(self.path)
        self.remote = FakePlaylist("PL-recovery-test")
        self.ids = ["AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC"]

    def matched_job(self, ids):
        rows = [("가수", f"곡 {i}", "앨범") for i in range(1, len(ids) + 1)]
        job = self.store.create("작업", rows)
        for seq, video in enumerate(ids, 1):
            self.store.result(job, seq, "High", [{"videoId": video}])
        self.store.destination(job, self.remote.playlist_id)
        return job

    @staticmethod
    def progress(*args):
        pass

    def test_reconcile_partial_remote_write_after_process_interruption(self):
        job = self.matched_job(self.ids)
        self.remote.interrupt_next_write = True
        with self.assertRaises(KeyboardInterrupt):
            register(self.remote, self.store, job, self.progress, delay=0)
        self.assertEqual(self.remote.contents, self.ids[:1])

        # A fresh instance can recover when the interrupted process recorded no acknowledgement.
        reopened = Checkpoint(self.path)
        register(self.remote, reopened, job, self.progress, delay=0)
        self.assertEqual(self.remote.add_requests[-1], self.ids[1:])
        self.assertEqual(self.remote.contents, self.ids)
        self.assertEqual([r["delivery"] for r in reopened.tracks(job)], ["existing", "added", "added"])

        previous_calls = list(self.remote.add_requests)
        register(self.remote, Checkpoint(self.path), job, self.progress, delay=0)
        self.assertEqual(self.remote.add_requests, previous_calls)

    def test_lost_create_response_does_not_create_a_second_playlist(self):
        job = self.store.create("새 작업", [("가수", "제목", "")])
        client = Mock()
        client.create_playlist.side_effect = TimeoutError("response lost")
        with self.assertRaises(PlaylistError):
            create(client, self.store, job, "새 목록", "", "PRIVATE")

        reopened = Checkpoint(self.path)
        self.assertTrue(reopened.job(job)["creating"])
        with self.assertRaises(PlaylistError):
            create(client, reopened, job, "새 목록", "", "PRIVATE")
        client.create_playlist.assert_called_once()

        reopened.destination(job, self.remote.playlist_id)
        recovered = reopened.job(job)
        self.assertEqual(recovered["playlist"], self.remote.playlist_id)
        self.assertFalse(recovered["creating"])

    def test_saved_deliveries_survive_stale_review_actions(self):
        job = self.matched_job(self.ids)
        register(self.remote, self.store, job, self.progress, only_seq=2, delay=0)
        reopened = Checkpoint(self.path)
        reopened.result(job, 2, "Not Found", [])
        reopened.choose(job, 2, self.ids[2])
        rows = reopened.tracks(job)
        self.assertEqual([r["delivery"] for r in rows], ["pending", "added", "pending"])
        self.assertEqual((rows[1]["status"], rows[1]["video"]), ("High", self.ids[1]))

        # An actual pending edit from the same review still applies.
        reopened.choose(job, 1, self.ids[2])
        self.assertEqual(reopened.tracks(job)[0]["video"], self.ids[2])


if __name__ == "__main__":
    unittest.main()
