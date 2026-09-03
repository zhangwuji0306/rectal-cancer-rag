"""Unit tests for heading-aware, lossless Markdown chunking."""

import unittest

from build_index import chunk_markdown


class WhitespaceTokenizer:
    """Small deterministic tokenizer for tests; no model download required."""

    def encode(self, text, add_special_tokens=False):
        return text.split()

    def decode(self, token_ids, skip_special_tokens=True,
               clean_up_tokenization_spaces=False):
        return " ".join(token_ids)


TOKENIZER = WhitespaceTokenizer()


class ChunkingTests(unittest.TestCase):
    def test_long_single_sentence_keeps_tail(self):
        words = [f"token-{i}" for i in range(1900)]
        text = " ".join(words)
        chunks = chunk_markdown(text, TOKENIZER, max_tokens=80, overlap=8)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(TOKENIZER.encode(c["text"])) <= 80 for c in chunks))
        chunk_tokens = {token for c in chunks for token in c["text"].split()}
        self.assertEqual(set(words), chunk_tokens)

    def test_headings_are_preserved(self):
        text = (
            "# Methods\n\n"
            "This is a useful paragraph with enough words to remain in the test output. "
            "It represents ordinary method content and should retain its heading.\n\n"
            "## Analysis\n\n"
            "Results are reported here with enough surrounding words to pass the noise "
            "threshold and verify section propagation in the chunk metadata."
        )
        chunks = chunk_markdown(text, TOKENIZER, max_tokens=80, overlap=8)

        self.assertEqual([c["section"] for c in chunks], ["Methods", "Methods > Analysis"])
        self.assertIn("useful paragraph", chunks[0]["text"])
        self.assertIn("Results", chunks[1]["text"])


if __name__ == "__main__":
    unittest.main()
