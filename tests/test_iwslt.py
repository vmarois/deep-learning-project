from unittest import TestCase

from dataset.iwslt import IWSLT, IWSLTLanguagePair
from dataset.utils import Split


class TestIWSLT(TestCase):
    def test_getitem(self):
        batch_size = 64
        language = IWSLTLanguagePair.fr_en

        source, targ = language.tokenizer()
        dataset = IWSLT(language_pair=language, split=Split.Train, split_ratio=0.6)
        source, target = dataset[0]

        # unit tests
        self.assertIsInstance(source, str)
        self.assertIsInstance(target, str)
