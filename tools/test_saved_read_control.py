import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).parent))
from check_saved_read_control import instance_pins


class NodeMappingTests(unittest.TestCase):
    def test_nested_definition_does_not_end_parent_early(self):
        lines=['.subckt OUTER A B','.subckt INNER C D','R1 C D 1','.ends INNER',
               'XI A N INNER','R2 N B 1','.ends OUTER','XO TOP BOT OUTER']
        record=instance_pins(lines,'XO')
        self.assertEqual(record['pins'],dict(A='TOP',B='BOT'))
        self.assertIn('R2 N B 1',record['body'])
        self.assertEqual(instance_pins(record['body'],'XI',definitions=lines)['pins'],dict(C='A',D='N'))

    def test_pin_count_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'pin count'):
            instance_pins(['.subckt INNER A B','R1 A B 1','.ends INNER','XI ONLY INNER'],'XI')


if __name__=='__main__':unittest.main(verbosity=2)
