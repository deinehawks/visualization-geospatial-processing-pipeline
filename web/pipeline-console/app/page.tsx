"use client";
import { useEffect, useRef, useState } from "react";
import RunForm from "../components/RunForm";
import RunDetail from "../components/RunDetail";
import Overview from "../components/Overview";
import { ConfirmationDialog } from "../components/Ui";
import { api, commandId, establishSession, Value } from "../lib/api";
import {
  newInputs,
  resumeInputs,
  passing,
  changedInput,
  currentRunOperation,
  savedTask4Profile,
} from "../lib/contract.mjs";

export default function Console() {
  const [page, setPage] = useState("dashboard"),
    [runs, setRuns] = useState<Value[]>([]),
    [operations, setOperations] = useState<Value[]>([]);
  const [legacy, setLegacy] = useState<string[]>([]),
    [detail, setDetail] = useState<Value | null>(null),
    [logs, setLogs] = useState<Value[]>([]);
  const [selected, setSelected] = useState<string | null>(null),
    [inputs, setInputs] = useState<Value>(newInputs()),
    [preflight, setPreflight] = useState<Value | null>(null);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [ready, setReady] = useState(false),
    [sending, setSending] = useState(false),
    [connected, setConnected] = useState(false);
  const [execution, setExecution] = useState<Value>({
    max_runs: 1,
    occupied: 0,
    available: 0,
    blocked: true,
  });
  const [availability, setAvailability] = useState(""),
    [before, setBefore] = useState<string | null>(null),
    [next, setNext] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<{
    title: string;
    body: string;
    phrase?: string;
    accept: (text: string) => Promise<void>;
  } | null>(null);
  const flight = useRef(false),
    boot = useRef<Promise<void> | null>(null);
  const command = async (action: () => Promise<void>) => {
    if (flight.current) return;
    flight.current = true;
    setSending(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(String(reason));
    } finally {
      flight.current = false;
      setSending(false);
    }
  };
  useEffect(() => {
    let active = true;
    boot.current ||= establishSession();
    boot.current
      .then(() => {
        if (active) setReady(true);
      })
      .catch((reason) => {
        if (active) setError(String(reason));
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!ready) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const [history, jobs] = await Promise.all([
          api(
            "/runs" + (before ? "?before=" + encodeURIComponent(before) : ""),
          ),
          api("/operations"),
        ]);
        if (active) {
          setConnected(true);
          setRuns(history.items);
          setNext(history.next_cursor);
          setAvailability(history.availability);
          setOperations(jobs.items);
          setLegacy(jobs.legacy_running);
          setExecution(jobs.execution);
        }
        if (selected) {
          const [value, events] = await Promise.all([
            api("/runs/" + encodeURIComponent(selected)),
            api("/runs/" + encodeURIComponent(selected) + "/logs"),
          ]);
          if (active) {
            setDetail(value);
            setLogs(events.items);
          }
        }
      } catch (reason) {
        if (active) {
          setConnected(false);
          setError("Console connection or data unavailable: " + String(reason));
        }
      } finally {
        if (active) timer = setTimeout(refresh, 2500);
      }
    };
    refresh();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [ready, selected, before]);
  useEffect(() => {
    if (preflight) {
      const latest = operations.find(
        (row) => row.operation_id === preflight.operation_id,
      );
      if (latest) setPreflight(latest);
    }
  }, [operations]);
  const lockReason =
    !ready || !connected
      ? "Waiting for the local console connection."
      : sending
        ? "Submitting another request."
        : legacy.length
          ? "An older terminal run needs review before starting."
          : execution.blocked
            ? "Review interrupted local ownership before starting."
            : execution.available <= 0
              ? "All local execution slots are occupied."
              : "";
  const maintenanceReason =
    execution.occupied > 0
      ? "Tile recovery requires all local slots to be free."
      : "";
  const current = detail
    ? currentRunOperation(detail.console_operations)
    : null;
  const newRun = () => {
    setInputs(newInputs());
    setPreflight(null);
    setPage("new");
    setNotice("");
  };
  const openRun = (runId: string) => {
    setSelected(runId);
    setDetail(null);
    setLogs([]);
    setPage("detail");
    setNotice("");
  };
  const update = (field: string, value: any) => {
    setInputs((old) => changedInput(old, field, value).inputs);
    setPreflight(null);
    setNotice("");
  };
  const check = () =>
    command(async () => {
      const value = await api("/preflight", {
        command_id: commandId(),
        inputs,
      });
      setPreflight(value);
      setOperations((old) => [value, ...old]);
    });
  const start = () =>
    command(async () => {
      if (!passing(preflight)) return;
      const value = await api("/start", {
        command_id: commandId(),
        preflight_id: preflight!.operation_id,
      });
      setOperations((old) => [value, ...old]);
      setPreflight(null);
      setPage("dashboard");
      setNotice(
        "Run request recorded. The runner checks inputs and storage again before processing.",
      );
    });
  const control = (action: string) => {
    if (!current) return;
    const operation = current;
    setConfirmation({
      title:
        action === "pause"
          ? "Pause the local pipeline?"
          : "Stop the local pipeline?",
      body: "This is a local cooperative request. WebODM is not canceled. A busy GIS operation may finish before the request takes effect.",
      accept: async () => {
        await api("/operations/" + operation.operation_id + "/controls", {
          command_id: commandId(),
          action,
        });
        setNotice(
          "Local " + action + " requested. Waiting for a safe checkpoint.",
        );
      },
    });
  };
  const decide = (action: string) => {
    if (!current?.review) return;
    const submit = async () => {
      await api("/operations/" + current.operation_id + "/review", {
        command_id: commandId(),
        review_id: current.review.review_id,
        action,
      });
      setNotice("Review decision submitted.");
    };
    if (action === "fail")
      setConfirmation({
        title: "Fail this quality review?",
        body: "The pipeline will record your failed review and stop this processing path.",
        accept: submit,
      });
    else void command(submit);
  };
  const reconcile = (operation: Value) =>
    setConfirmation({
      title: "Review interrupted local operation",
      body: "Confirm that the runner and all its child processes have stopped. This resolves local ownership only; it does not change WebODM or resume the run.",
      phrase: "RECONCILE " + operation.operation_id,
      accept: async (text) => {
        await api("/operations/" + operation.operation_id + "/reconcile", {
          confirmation: text,
        });
        setNotice(
          "Local operation resolved. Review the run before preparing a resume.",
        );
      },
    });
  const title =
    page === "dashboard"
      ? "Pipeline overview"
      : page === "detail"
        ? "Run workspace"
        : inputs.action === "new"
          ? "Prepare a new run"
          : inputs.action === "resume"
            ? "Resume processing"
            : "Rebuild survey tiles";
  return (
    <div className="workspace">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark">V</span>
          <div>
            Pipeline Console<small>SURVEY PROCESSING</small>
          </div>
        </div>
        <nav aria-label="Main navigation">
          <button
            className={page === "dashboard" ? "selected" : ""}
            aria-current={page === "dashboard" ? "page" : undefined}
            onClick={() => setPage("dashboard")}
          >
            <span aria-hidden="true">▦</span>Overview &amp; runs
          </button>
          <button
            className={page === "new" ? "selected" : ""}
            aria-current={page === "new" ? "page" : undefined}
            onClick={newRun}
          >
            <span aria-hidden="true">＋</span>New run
          </button>
          {selected && (
            <button
              className={page === "detail" ? "selected" : ""}
              aria-current={page === "detail" ? "page" : undefined}
              onClick={() => setPage("detail")}
            >
              <span aria-hidden="true">▤</span>Run workspace
            </button>
          )}
        </nav>
        <div className="rail-bottom">
          <strong>This workstation only</strong>
          <small>
            {execution.max_runs} local execution slot(s)
            <br />
            WebODM may continue while paused.
          </small>
        </div>
      </aside>
      <main>
        <div className="main-content">
          <header>
            <div>
              <p className="eyebrow">OPERATOR WORKSPACE</p>
              <h1>{title}</h1>
              <p className="header-description">
                {page === "dashboard"
                  ? "Start work, follow progress, and review survey results."
                  : page === "detail"
                    ? "Current progress and the next safe action for this survey."
                    : "Choose inputs, check storage, then review before processing."}
              </p>
            </div>
            <div className={"local-badge " + (connected ? "connected" : "")}>
              <span className="status-dot" />
              {connected ? "Local console connected" : "Connecting to console"}
            </div>
          </header>
          {error && (
            <div role="alert" className="alert error">
              <strong>Attention needed</strong>
              <p>{error}</p>
              <button onClick={() => setError("")}>Dismiss</button>
            </div>
          )}
          {notice && (
            <div role="status" className="alert">
              <p>{notice}</p>
            </div>
          )}
          {!!legacy.length && (
            <div className="alert warning">
              <strong>Older terminal execution needs review</strong>
              <p>
                Monitor the original terminal until it stops or pauses. The
                console cannot take over its live process.
              </p>
              <details>
                <summary>Technical run IDs</summary>
                <code>{legacy.join("\n")}</code>
              </details>
            </div>
          )}
          {!!execution.legacy_reconciled?.length && (
            <p className="small muted">
              {execution.legacy_reconciled.length} historical run(s) reconciled
              locally. Their persisted status and remote WebODM state are
              unchanged.
            </p>
          )}
          {page === "dashboard" && (
            <Overview
              {...{
                runs,
                operations,
                execution,
                availability,
                before,
                next,
                openRun,
                newRun,
                reconcile,
              }}
              firstPage={() => setBefore(null)}
              older={() => setBefore(next)}
            />
          )}
          {page === "new" && (
            <RunForm
              {...{ inputs, preflight, update, check, start }}
              lockReason={
                lockReason ||
                (inputs.action === "rebuild_tiles" ? maintenanceReason : "")
              }
              resumeKnownOptions={detail?.resume_known_options || []}
              savedProfile={
                inputs.action === "new" ? null : savedTask4Profile(detail)
              }
              boundTask4={
                inputs.action !== "new" &&
                !!detail?.operations?.some(
                  (row: Value) => row.operation_key === "task4" && row.task_id,
                )
              }
            />
          )}
          {page === "detail" && detail && (
            <RunDetail
              key={detail.run.run_id}
              {...{
                detail,
                logs,
                lockReason,
                maintenanceReason,
                sending,
                control,
                decide,
                reconcile,
              }}
              resume={(recovery = false) => {
                setInputs(resumeInputs(detail, recovery));
                setPreflight(null);
                setPage("new");
                setNotice("");
              }}
              copy={(path) =>
                void command(async () => {
                  await navigator.clipboard.writeText(path);
                  setNotice("Path copied.");
                })
              }
            />
          )}
          {page === "detail" && !detail && (
            <div role="status" className="empty">
              Loading this survey run…
            </div>
          )}
          <footer>
            Pipeline Console · Local controls and WebODM processing have
            separate lifecycles.
          </footer>
        </div>
      </main>
      {confirmation && (
        <ConfirmationDialog
          title={confirmation.title}
          body={confirmation.body}
          phrase={confirmation.phrase}
          onCancel={() => setConfirmation(null)}
          onAccept={(text) => {
            const pending = confirmation;
            setConfirmation(null);
            void command(() => pending.accept(text));
          }}
        />
      )}
    </div>
  );
}
