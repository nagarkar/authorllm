"""Throwaway: proves the tests workflow turns red on a failing test (issue #363).
Removed in the next commit.
"""
import sys

print("FAIL: deliberate failure, to show a red check on the PR")
sys.exit(1)
