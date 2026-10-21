"""Windows adapter ABI/branch tests with all kernel calls replaced by fakes."""
import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import operator_ui.storage as storage


@pytest.fixture
def kernel(monkeypatch):
    # Replace this module's platform boundary, not process-global os.name.
    monkeypatch.setattr(storage,'os',SimpleNamespace(name='nt'))
    methods = ('CreateMutexW','WaitForSingleObject','ReleaseMutex','CloseHandle','OpenProcess',
               'GetProcessTimes','GetExitCodeProcess','OpenJobObjectW','QueryInformationJobObject',
               'CreateJobObjectW','GetCurrentProcess','AssignProcessToJobObject')
    value = SimpleNamespace(**{name:Mock(return_value=1) for name in methods})
    value.WaitForSingleObject.return_value=0
    monkeypatch.setattr(storage.ctypes,'WinDLL',lambda *_,**__:value,raising=False)
    monkeypatch.setattr(storage.ctypes,'get_last_error',lambda:87,raising=False)
    return value


@pytest.mark.parametrize('result',[0,128])
def test_mutex_releases_handles_and_abandoned_mutex_still_checks_journals(kernel,tmp_path,result):
    kernel.WaitForSingleObject.return_value=result
    with storage.WindowsMutex(tmp_path) as mutex:
        assert mutex.handle==1
    kernel.ReleaseMutex.assert_called_once_with(1)
    kernel.CloseHandle.assert_called_once_with(1)
    assert kernel.CreateMutexW.call_args.args[2].startswith('Global\\VizPipeline-')


def test_busy_mutex_closes_handle_without_releasing_unowned_lock(kernel,tmp_path):
    kernel.WaitForSingleObject.return_value=258
    with pytest.raises(storage.ConsoleError,match='Another command'):
        with storage.WindowsMutex(tmp_path):
            pytest.fail('Busy mutex acquired')
    kernel.ReleaseMutex.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(1)


def test_process_identity_uses_creation_time_and_exit_status(kernel):
    def exit_code(_handle,target):
        target._obj.value=259
        return 1
    def times(_handle,created,*_):
        created._obj.value=123456
        return 1
    kernel.GetExitCodeProcess.side_effect=exit_code
    kernel.GetProcessTimes.side_effect=times
    processes=storage.WindowsProcesses()
    assert processes.identity(42)=='123456'
    kernel.GetExitCodeProcess.side_effect=lambda _handle,target:setattr(target._obj,'value',0) or 1
    assert processes.identity(42) is None
    assert kernel.CloseHandle.call_count==2


def test_process_access_denied_is_unknown_not_dead(kernel,monkeypatch):
    kernel.OpenProcess.return_value=0
    processes=storage.WindowsProcesses()
    assert processes.identity(42) is None
    monkeypatch.setattr(storage.ctypes,'get_last_error',lambda:5)
    assert processes.identity(42)=='unknown'


def test_job_query_counts_descendants_and_closes_handle(kernel):
    def query(_handle,kind,target,size,_returned):
        assert kind==1 and size==48
        target._obj.active=3
        return 1
    kernel.QueryInformationJobObject.side_effect=query
    assert storage.WindowsProcesses().job_active('owned-job')==3
    kernel.OpenJobObjectW.assert_called_once_with(4,False,'owned-job')
    kernel.CloseHandle.assert_called_once_with(1)


def test_failed_job_query_cannot_authorize_new_work(kernel):
    kernel.QueryInformationJobObject.return_value=0
    assert storage.WindowsProcesses().job_active('owned-job') is None


def test_job_membership_failure_is_fail_closed(kernel):
    kernel.AssignProcessToJobObject.return_value=0
    with pytest.raises(storage.ConsoleError,match='descendant ownership'):
        storage.WindowsProcesses().join_job('owned-job')
    kernel.CloseHandle.assert_called_once_with(1)


def test_named_job_has_no_browser_lifetime_kill_policy(kernel):
    processes=storage.WindowsProcesses()
    actual,handle=processes.join_job('owned-job')
    assert actual is kernel and handle==1
    kernel.CreateJobObjectW.assert_called_once_with(None,'owned-job')
    kernel.AssignProcessToJobObject.assert_called_once_with(1,1)
    # No SetInformationJobObject kill-on-close configuration exists in this adapter.
    assert not hasattr(kernel,'SetInformationJobObject')
