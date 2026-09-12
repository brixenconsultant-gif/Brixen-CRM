import os
import sys
import unittest

sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

def run_test_suite():
    print("==================================================")
    print("STARTING BRIXEN CRM MODULAR COMPONENT TEST SUITE")
    print("==================================================")
    
    loader = unittest.TestLoader()
    start_dir = os.path.dirname(os.path.abspath(__file__))
    suite = loader.discover(start_dir, pattern="test_*.py")
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("\n==================================================")
        print("ALL COMPONENT TESTS PASSED 100%! SUCCESS!")
        print("==================================================")
        return 0
    else:
        print("\n==================================================")
        print(f"TEST SUITE FAILED with {len(result.failures)} failures and {len(result.errors)} errors.")
        print("==================================================")
        return 1

if __name__ == '__main__':
    sys.exit(run_test_suite())
