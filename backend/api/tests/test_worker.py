import unittest

from worker import decode_message


class WorkerMessageTests(unittest.TestCase):
    def test_decodes_json_object(self):
        self.assertEqual(decode_message('{"kind":"agent","job_id":"job-1"}'),
                         {"kind": "agent", "job_id": "job-1"})

    def test_rejects_non_object_json(self):
        for body in ('[]', 'null', '"text"', '42'):
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, "JSON object"):
                    decode_message(body)

    def test_rejects_malformed_json(self):
        with self.assertRaises(ValueError):
            decode_message('{"kind":')


if __name__ == "__main__":
    unittest.main()
