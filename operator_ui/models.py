"""Small allowlisted API inputs. No shell/CLI passthrough fields."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class RunInput(Input):
    action: Literal['new', 'resume', 'rebuild_tiles'] = 'new'
    survey: str = Field(default='', max_length=1024)
    survey_id: str | None = Field(default=None, pattern=r'^[A-Z]{2,5}-[0-9]{6}$')
    year: int = Field(default=2026, ge=2000, le=2100)
    mode: Literal['task4', 'task2', 'both'] | None = None
    run_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,100}$')
    date: str | None = Field(default=None, pattern=r'^[0-9]{8}$')
    uav: str | None = Field(default=None, pattern=r'^[A-Za-z0-9 _-]{1,100}$')
    node_id: int | None = Field(default=None, ge=1)
    disable_cross_run: bool = False
    rgb: bool = False
    keep_workspace: bool = False
    rebuild_task: Literal['task2', 'task4'] | None = None
    confirmation: str = Field(default='', max_length=300)


class PreflightCommand(Input):
    command_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    inputs: RunInput


class StartCommand(Input):
    command_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    preflight_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class ControlCommand(Input):
    command_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    action: Literal['pause', 'stop']


class ReviewCommand(Input):
    command_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    review_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    action: Literal['approve', 'fail', 'restart']
    task: Literal['task1', 'task2', 'task4'] | None = None
    restart_from: str | None = Field(default=None, max_length=50)
    confirmation: str = Field(default='', max_length=300)


class ReconcileCommand(Input):
    confirmation: str = Field(max_length=200)
