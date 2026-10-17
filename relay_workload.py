#!/usr/bin/env python3
"""Paced JP/CJ synthetic workload; every controller owns a fresh isolated relay."""
from relay_workload_plan import (make_plan, clock_mapping, publish_logical,
    advance_cursor, ssh_argv, validate_hello, overlap_count, seed_history)


if __name__ == '__main__':
    from relay_workload_controller import main
    main()
