import os
import sys

sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from tests.run_all import run_test_suite

if __name__ == '__main__':
    sys.exit(run_test_suite())
