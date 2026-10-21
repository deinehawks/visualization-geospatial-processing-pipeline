"""UPSDiscord-specific exceptions: fake process/CIM boundaries and owned paths."""
import ctypes
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_pipeline_console import console, seed, uid
from operator_ui.legacy_reconciliation import reconcile_legacy
from operator_ui.storage import ConsoleError, read_json
from operator_ui.ups_process import ups_identity, windows_ups_identity, system_directory
from tools.pipeline_control import main


EXE = r'C:\Users\Example\Python313\python.exe'
PID = 1448


def identity(**changes):
    return {'pid': PID, 'birth': '134054578420000000', 'executable': EXE,
            'command_line': f'"{EXE}"  UPSDiscord.py', **changes}


def inventory(*extra):
    return lambda: [{'pid':os.getpid(), 'name':'python.exe'}, {'pid':PID, 'name':'python.exe'}, *extra]


def options():
    return {'inventory':inventory(), 'allow_ups_discord_pid':PID,
            'ups_inspector':lambda _: identity()}


def preview(console, **changes):
    return reconcile_legacy(console.store, console.reader.database, 'old', **{**options(), **changes})


def apply(console, **changes):
    phrase = preview(console)['confirmation']
    return preview(console, confirmation=phrase, **changes)


def test_explicit_flag_exempts_only_verified_invocation_and_receipt_is_local(console):
    seed(console, 'old', 'running')
    before = console.reader.database.read_bytes()
    result = preview(console)
    assert not result['applied'] and not result['process_blockers']
    assert 'ALLOW VERIFIED UPSDISCORD 1448' in result['confirmation']
    assert result['ups_discord_exception'] == ups_identity(identity())
    assert not console.store.root.exists()
    assert apply(console)['applied']
    receipt = read_json(next(console.store.root.glob('legacy-reconciled-*.json')))
    assert receipt['ups_discord_exception'] == ups_identity(identity())
    assert receipt['process_check'] == 'toolhelp_with_verified_ups_exception'
    assert console.legacy_running() == [] and not console.execution_status()['blocked']
    assert console.reader.database.read_bytes() == before
    assert apply(console)['already_applied']


def test_default_still_blocks_ups_and_does_not_probe_without_opt_in(console):
    seed(console, 'old', 'running')
    probe = Mock(side_effect=AssertionError('No opt-in'))
    result = reconcile_legacy(console.store, console.reader.database, 'old', inventory=inventory(), ups_inspector=probe)
    assert result['process_blockers'] == [{'pid':PID, 'name':'python.exe'}]
    assert 'ups_discord_exception' not in result
    probe.assert_not_called()


@pytest.mark.parametrize('name', ['python.exe','qgis-bin.exe','gdal_translate.exe'])
def test_exception_never_exempts_other_interpreters_or_gis(console, name):
    seed(console, 'old', 'running')
    processes = inventory({'pid':5566, 'name':name})
    assert preview(console, inventory=processes)['process_blockers'] == [{'pid':5566,'name':name}]
    with pytest.raises(ConsoleError) as failure:
        apply(console, inventory=processes)
    assert failure.value.code == 'BUSY'
    assert not console.store.root.exists()


@pytest.mark.parametrize('command', [
    f'"{EXE}" main.py', f'"{EXE}" -c "print(1)"', f'"{EXE}" -m operator_ui.runner',
    f'"{EXE}" -i UPSDiscord.py', f'"{EXE}" UPSDiscord.py --run-id old',
    f'"{EXE}" notUPSDiscord.py', f'"{EXE}" UPSDiscord.py;main.py',
    f'"{EXE}" relative\\UPSDiscord.py', f'"{EXE}" UPSDiscord.py\n',
    f'"{EXE}" UPSDiscord.py\x00', '', None])
def test_wrong_script_flags_extra_args_or_unknown_command_rejected(command):
    with pytest.raises(ConsoleError):
        ups_identity(identity(command_line=command))


@pytest.mark.parametrize('fields', [
    {'pid':0}, {'pid':-1}, {'pid':True}, {'pid':2**32}, {'birth':'unknown'}, {'birth':None},
    {'executable':None}, {'executable':'python.exe'}, {'executable':r'C:\cmd.exe'},
    {'executable':r'C:\different\python.exe'}, {'fingerprint':'0'*64}])
def test_invalid_identity_cannot_be_exempted(fields):
    with pytest.raises(ConsoleError):
        ups_identity(identity(**fields))


def test_absolute_quoted_script_and_case_insensitive_windows_path_supported():
    proof = ups_identity(identity(command_line=f'"{EXE.upper()}" "D:\\UPS Utility\\UPSDiscord.py"'))
    assert proof['pid'] == PID and proof['executable'] == EXE.casefold()
    assert ups_identity(proof) == proof


@pytest.mark.parametrize('change', [
    {'birth':'134054578420000001'},
    {'executable':r'C:\Other\python.exe', 'command_line':r'"C:\Other\python.exe" UPSDiscord.py'},
    {'command_line':f'"{EXE}" UPSDiscord.py '}])
def test_stale_confirmation_cannot_authorize_pid_reuse_or_changed_identity(console, change):
    seed(console, 'old', 'running')
    phrase = preview(console)['confirmation']
    with pytest.raises(ConsoleError) as failure:
        preview(console, confirmation=phrase, ups_inspector=lambda _:identity(**change))
    assert failure.value.code == 'CONFIRMATION_REQUIRED'
    assert not console.store.root.exists()


def test_identity_change_during_apply_fails_closed(console):
    seed(console, 'old', 'running')
    probe = Mock(side_effect=[identity(), identity(), identity(birth='134054578420000001')])
    with pytest.raises(ConsoleError) as failure:
        apply(console, ups_inspector=probe)
    assert failure.value.code == 'STALE_PROCESS_EXCEPTION'
    assert not console.store.root.exists()


def test_new_python_during_final_cim_query_is_seen_in_later_snapshot(console):
    seed(console, 'old', 'running')
    phrase = preview(console)['confirmation']
    state = {'calls':0, 'new_python':False}
    def inspect(_):
        state['calls'] += 1
        state['new_python'] = state['calls'] >= 3
        return identity()
    def processes():
        return inventory(*([{'pid':5566, 'name':'python.exe'}] if state['new_python'] else []))()
    with pytest.raises(ConsoleError, match='appeared'):
        preview(console, confirmation=phrase, ups_inspector=inspect, inventory=processes)
    assert not console.store.root.exists()


def test_oversized_exception_does_not_write_an_unreadable_receipt(console):
    seed(console, 'old', 'running')
    executable = 'C:\\' + '\u754c' * 3000 + '\\python.exe'
    inspect = lambda _: identity(executable=executable, command_line=f'"{executable}" UPSDiscord.py')
    phrase = preview(console, ups_inspector=inspect)['confirmation']
    with pytest.raises(ConsoleError, match='size limit'):
        preview(console, confirmation=phrase, ups_inspector=inspect)
    assert not list(console.store.root.glob('legacy-reconciled-*.json'))


@pytest.mark.parametrize('rows', [
    [{'pid':os.getpid(),'name':'python.exe'}],
    [{'pid':os.getpid(),'name':'python.exe'}, {'pid':PID,'name':'qgis.exe'}]])
def test_exception_pid_missing_or_wrong_toolhelp_name_is_rejected(console, rows):
    seed(console, 'old', 'running')
    with pytest.raises(ConsoleError) as failure:
        preview(console, inventory=lambda:rows)
    assert failure.value.code == 'STALE_PROCESS_EXCEPTION'


def test_self_or_mismatched_probe_pid_cannot_be_exempted(console):
    seed(console, 'old', 'running')
    with pytest.raises(ConsoleError):
        preview(console, allow_ups_discord_pid=os.getpid())
    with pytest.raises(ConsoleError):
        preview(console, ups_inspector=lambda _: identity(pid=PID+1))
    assert not console.store.root.exists()


def test_owned_surviving_children_are_not_bypassed(console):
    seed(console, 'old', 'running')
    operation, _ = console.store.reserve(uid(), {'kind':'cli','run_id':'other'})
    console.store.update(operation['operation_id'], state='finished')
    console.store.processes.jobs[operation['job_name']] = 1
    with pytest.raises(ConsoleError, match='Owned or uncertain'):
        apply(console)
    assert not list(console.store.root.glob('legacy-reconciled-*.json'))


def test_old_confirmation_cannot_add_or_omit_exception(console):
    seed(console, 'old', 'running')
    plain = reconcile_legacy(console.store, console.reader.database, 'old', inventory=inventory())
    with pytest.raises(ConsoleError, match='stale'):
        preview(console, confirmation=plain['confirmation'])
    phrase = preview(console)['confirmation']
    with pytest.raises(ConsoleError, match='stale'):
        reconcile_legacy(console.store, console.reader.database, 'old', inventory=inventory(), confirmation=phrase)


def test_tampered_exception_receipt_cannot_unblock_admission(console):
    seed(console, 'old', 'running')
    apply(console)
    path = next(console.store.root.glob('legacy-reconciled-*.json'))
    value = read_json(path)
    value['ups_discord_exception']['birth'] = '123'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ConsoleError) as failure:
        console.store.verify_legacy(console.reader.database)
    assert failure.value.code == 'INVALID_JOURNAL'


def test_cli_forwards_exception_only_to_reconcile_command(console, capsys):
    seed(console, 'old', 'running')
    kwargs = {'store':console.store, 'database':console.reader.database,
              'inventory':inventory(), 'ups_inspector':lambda _:identity()}
    argv = ['reconcile-legacy','--run-id','old','--allow-ups-discord-pid',str(PID)]
    result = main(argv, **kwargs)
    assert result['ups_discord_exception']['pid'] == PID
    assert main(argv + ['--confirmation',result['confirmation']], **kwargs)['applied']
    assert 'ALLOW VERIFIED UPSDISCORD' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(['configure','--max-runs','2','--allow-ups-discord-pid',str(PID)], **kwargs)


@pytest.fixture
def native(monkeypatch):
    import operator_ui.ups_process as module
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt', getpid=os.getpid))
    result = SimpleNamespace(returncode=0, stdout=json.dumps({'ProcessId':PID, 'ExecutablePath':EXE,
                              'CommandLine':identity()['command_line']}))
    return {'runner':Mock(return_value=result),
            'processes':SimpleNamespace(identity=Mock(return_value=identity()['birth'])),
            'directory':lambda:r'C:\Windows\System32'}


def test_cim_query_is_pid_scoped_no_shell_profile_or_window_and_rechecks_birth(native):
    assert windows_ups_identity(PID, **native) == ups_identity(identity())
    args, kwargs = native['runner'].call_args
    assert args[0][0] == r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    assert args[0][1:5] == ['-NoLogo','-NoProfile','-NonInteractive','-Command']
    assert 'ProcessId = 1448' in args[0][-1]
    assert 'Terminate' not in args[0][-1] and 'UPSDiscord.py' not in args[0][-1]
    assert kwargs['shell'] is False and kwargs['stdin'] == subprocess.DEVNULL
    assert kwargs['timeout'] == 10 and kwargs['creationflags'] == 0x08000000
    assert native['processes'].identity.call_args_list[0].args == (PID,)
    assert native['processes'].identity.call_count == 2


@pytest.mark.parametrize('failure', ['denied','exited','reused','command_hidden','wrong_pid','invalid_json','nonzero','timeout','spawn','encoding'])
def test_cim_or_process_identity_failure_cannot_grant_exception(native, failure):
    if failure == 'denied':
        native['processes'].identity.return_value = 'unknown'
    elif failure == 'exited':
        native['processes'].identity.return_value = None
    elif failure == 'reused':
        native['processes'].identity.side_effect = [identity()['birth'], '123']
    elif failure == 'command_hidden':
        native['runner'].return_value.stdout = json.dumps({'ProcessId':PID,'ExecutablePath':EXE,'CommandLine':None})
    elif failure == 'wrong_pid':
        native['runner'].return_value.stdout = json.dumps({'ProcessId':PID+1})
    elif failure == 'invalid_json':
        native['runner'].return_value.stdout = 'PRIVATE-OUTPUT-not-json'
    elif failure == 'nonzero':
        native['runner'].return_value.returncode = 1
    elif failure == 'timeout':
        native['runner'].side_effect = subprocess.TimeoutExpired('fixed-query',10)
    elif failure == 'spawn':
        native['runner'].side_effect = OSError('cannot launch inspector')
    else:
        native['runner'].side_effect = UnicodeError('PRIVATE-OUTPUT')
    with pytest.raises(ConsoleError) as error:
        windows_ups_identity(PID, **native)
    assert 'PRIVATE-OUTPUT' not in str(error.value)


def test_native_system_directory_uses_api_not_path_search(monkeypatch):
    kernel = SimpleNamespace(GetSystemDirectoryW=Mock())
    def directory(buffer, size):
        assert size == 32768
        buffer.value = r'C:\Windows\System32'
        return len(buffer.value)
    kernel.GetSystemDirectoryW.side_effect = directory
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_,**__:kernel, raising=False)
    assert system_directory() == r'C:\Windows\System32'


@pytest.mark.parametrize('size', [0,32768,32769])
def test_unverified_system_directory_cannot_select_inspector(monkeypatch, size):
    kernel = SimpleNamespace(GetSystemDirectoryW=Mock(return_value=size))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_,**__:kernel, raising=False)
    with pytest.raises(ConsoleError):
        system_directory()
