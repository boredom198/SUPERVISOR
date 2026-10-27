import tempfile
import unittest
from pathlib import Path

from supervisor.language_inspector import LanguageInspector


class TestLanguageInspector(unittest.TestCase):
    def test_detects_common_calculator_logic_regressions(self):
        with tempfile.TemporaryDirectory() as tmp:
            inspector = LanguageInspector(tmp)
            code = '''
case '-':
  computation = current - prev;
  break;
case '/':
  if (current === 0) {
    this.currentOperand = '';
    return;
  }
  computation = prev / current;
  break;
case '^':
  computation = Math.pow(prev, current - 1);
  break;
'''
            findings = inspector.inspect('calculator.js', code)
            descriptions = [f.description for f in findings]
            self.assertTrue(any('operands may be reversed' in d for d in descriptions))
            self.assertTrue(any('zero-value guard' in d for d in descriptions))
            self.assertTrue(any('decremented by one' in d for d in descriptions))

    def test_ignores_unrelated_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            findings = LanguageInspector(tmp).inspect('clean.js', 'const total = a + b;\n')
            self.assertEqual(findings, [])


if __name__ == '__main__':
    unittest.main()
