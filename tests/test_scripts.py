import unittest

from langdetect2.scripts import (
    HANGUL, HAN, HIRAGANA, KATAKANA, LATIN, CYRILLIC, ARABIC, script_of,
    count_scripts,
)


class ScriptTest(unittest.TestCase):
    def test_basic_scripts(self):
        self.assertEqual(script_of("あ"), HIRAGANA)
        self.assertEqual(script_of("ア"), KATAKANA)
        self.assertEqual(script_of("ー"), KATAKANA)      # 长音符号归日语
        self.assertEqual(script_of("한"), HANGUL)
        self.assertEqual(script_of("汉"), HAN)
        self.assertEqual(script_of("a"), LATIN)
        self.assertEqual(script_of("я"), CYRILLIC)
        self.assertEqual(script_of("ع"), ARABIC)

    def test_punctuation_and_digits_ignored(self):
        self.assertIsNone(script_of("，"))
        self.assertIsNone(script_of("1"))
        self.assertIsNone(script_of(" "))

    def test_count_scripts(self):
        counts = count_scripts("ab 汉字 あ")
        self.assertEqual(counts[LATIN], 2)
        self.assertEqual(counts[HAN], 2)
        self.assertEqual(counts[HIRAGANA], 1)


if __name__ == "__main__":
    unittest.main()
