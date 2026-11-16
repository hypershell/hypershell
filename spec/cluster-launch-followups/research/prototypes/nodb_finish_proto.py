"""Prototype integration test (R5): a `--no-db` cluster whose client returns every result, then exits 1 before FINISHED."""
from __future__ import annotations

import os
import sys
import shlex
import time
from pathlib import Path
from subprocess import Popen, TimeoutExpired, DEVNULL

from pytest import mark

from tests.conftest import free_port
from tests.test_cluster_exit import kill_group

HS = os.path.join(os.path.dirname(sys.executable), 'hs')
ADDRESS = '127.0.0.1'
TASKS = 5

STILL_RUNNING_AT = 13.0
"""Seconds from the start at which the cluster must still be running: past R1's 10 s bound (the base exits ~5 s)."""

# A real client patched to exit 1 in place of its FINISHED heartbeat, after the collector has returned every
# result. Run by the launcher as `python crashy.py <remote-exe> client ...`: argv[1] (the remote exe) is dropped.
CRASHY_CLIENT = """\
import os, sys
import hypershell.client as client
def crash(self):
    sys.stderr.write('crashy client: results returned, exiting 1 before FINISHED\\n')
    sys.stderr.flush()
    os._exit(1)
client.ClientThread.wait_heartbeat = crash
from hypershell import main
sys.argv = ['hs', *sys.argv[2:]]
sys.exit(main())
"""


@mark.integration
def test_no_db_cluster_whose_client_dies_after_its_last_result_is_not_a_failed_launch(temp_site: Path) -> None:
    """R5: every result on standard output, the exit warned about, no R1 error; still running past 10 s."""
    site = Path(temp_site)
    (site / 'tasks.txt').write_text(''.join(f'echo {n}\n' for n in range(TASKS)))
    crashy = site / 'crashy.py'
    crashy.write_text(CRASHY_CLIENT)
    launcher = f'{shlex.quote(sys.executable)} {shlex.quote(str(crashy))}'
    out_path, err_path = site / 'cluster.out', site / 'cluster.err'
    with open(out_path, mode='w') as out, open(err_path, mode='w') as err:
        started = time.monotonic()
        env = dict(os.environ)
        if os.environ.get('CLF_STANDIN_R5') == '1':  # PROTOTYPE ONLY: an oracle in place of the fix
            env['PYTHONPATH'] = os.pathsep.join([str(Path(__file__).parent / 'standin_site'), env.get('PYTHONPATH', '')])
        proc = Popen([HS, 'cluster', 'tasks.txt', '--no-db', '--no-tls', '-H', ADDRESS, '-p', str(free_port()),
                      '--launcher', launcher], cwd=site, env=env, stdin=DEVNULL, stdout=out, stderr=err,
                     start_new_session=True)
        try:
            try:
                proc.wait(timeout=STILL_RUNNING_AT)
                ended = time.monotonic() - started
            except TimeoutExpired:
                ended = None
        finally:
            kill_group(proc)
    log_text = err_path.read_text()
    print(f'TIMES ended={ended and round(ended, 1)} rc={proc.returncode}')
    assert sorted(out_path.read_text().split()) == [str(n) for n in range(TASKS)], log_text[-4000:]
    assert 'crashy client: results returned' in log_text, log_text[-4000:]
    assert 'every launched client has exited with tasks remaining' not in log_text, log_text[-4000:]
    assert ended is None, f'exited {proc.returncode} after {ended:.1f}s:\n' + log_text[-4000:]
    assert any('WARNING' in line and 'exited with status 1' in line for line in log_text.splitlines()), log_text[-4000:]
