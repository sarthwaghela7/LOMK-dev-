import unittest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from routes.calls import get_audio


class AudioRouteTests(unittest.TestCase):
    def test_audio_endpoint_returns_503_when_database_is_unavailable(self):
        class BrokenSession:
            def query(self, *_args, **_kwargs):
                raise OperationalError("statement", {}, Exception("database unavailable"))

        with self.assertRaises(HTTPException) as context:
            get_audio(call_id="test-call-id", db=BrokenSession())

        self.assertEqual(context.exception.status_code, 503)
        self.assertIn("temporarily unavailable", str(context.exception.detail).lower())


if __name__ == "__main__":
    unittest.main()
