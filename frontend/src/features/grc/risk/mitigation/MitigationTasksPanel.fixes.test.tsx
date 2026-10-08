/**
 * MitigationTasksPanel — failures that are not API errors. A dropped
 * connection rejects with a plain `TypeError`, and anything can throw a
 * non-`Error`; the panel must say something either way instead of swallowing
 * it, and a failed load must never read as "no tasks".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MitigationTask, MitigationTaskOccurrence } from "@/types";

vi.mock("@/api/client", () => import("@/test/apiMock").then((m) => m.apiClientModule()));
vi.mock("@/hooks/useDateFormat", () => import("@/test/hooks").then((m) => m.useDateFormatModule()));
vi.mock("@/features/grc/risk/mitigation/taskHistoryExport", () => ({ exportTaskHistory: vi.fn() }));

import { mockApi } from "@/test/apiMock";
import { installConfirm } from "@/test/dom";
import { USERS } from "@/test/fixtures/metamodel";
import { hookState } from "@/test/hooks";
import MitigationTasksPanel from "./MitigationTasksPanel";

const USER_OPTIONS = USERS.slice(0, 2).map((u) => ({ id: u.id, email: u.email, display_name: u.display_name }));
const ME = USER_OPTIONS[0].id;

function occ(
  overrides: Partial<MitigationTaskOccurrence> & { id: string; sequence: number },
): MitigationTaskOccurrence {
  return {
    task_id: "t1",
    assigned_owner_id: null,
    assigned_owner_name: null,
    due_date: "2030-06-15",
    status: "open",
    activated_at: null,
    completed_at: null,
    completed_by: null,
    completed_by_name: null,
    owner_at_completion: null,
    owner_at_completion_name: null,
    completion_notes: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

function task(overrides: Partial<MitigationTask> & { id: string; title: string }): MitigationTask {
  return {
    reference: "T-000001",
    risk_id: "r1",
    description: null,
    owner_id: null,
    owner_name: null,
    recurrence_unit: "none",
    recurrence_interval: 1,
    lead_time_days: 0,
    is_active: true,
    created_by: null,
    created_at: null,
    updated_at: null,
    occurrences: [],
    ...overrides,
  };
}

const OPEN = task({ id: "t1", title: "Review access rights", occurrences: [occ({ id: "o1", sequence: 1 })] });
const SCHEDULED = task({
  id: "t2",
  title: "Quarterly DR test",
  recurrence_unit: "months",
  recurrence_interval: 3,
  occurrences: [occ({ id: "o2", sequence: 1, status: "scheduled", task_id: "t2" })],
});

/** What a dropped connection rejects with — not an `ApiError`. */
const offline = () => () => {
  throw new TypeError("Failed to fetch");
};
/** A rejection that is not an `Error` at all. */
const throwsString = () => () => {
  throw "nope";
};

const EMPTY_STATE = /No mitigation tasks yet/;

function renderPanel() {
  const user = userEvent.setup();
  render(
    <MitigationTasksPanel
      riskId="r1"
      riskReference="R-000001"
      riskClosed={false}
      users={USER_OPTIONS}
      currentUserId={ME}
    />,
  );
  return { user };
}

const rowOf = (title: string) => screen.getByText(title).closest(".MuiBox-root") as HTMLElement;
const button = (scope: HTMLElement, icon: string) => within(scope).getByRole("button", { name: icon });

let confirmSpy: ReturnType<typeof installConfirm>;

beforeEach(() => {
  mockApi.reset();
  hookState.reset();
  confirmSpy = installConfirm(true);
});

afterEach(() => {
  confirmSpy.mockRestore();
});

describe("MitigationTasksPanel — a failed load", () => {
  it("shows a network failure, and not the empty state", async () => {
    mockApi.on("get", "/risks/r1/mitigation-tasks", offline());
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
    expect(screen.queryByText(EMPTY_STATE)).not.toBeInTheDocument();
    expect(screen.queryByText("Loading mitigation tasks…")).not.toBeInTheDocument();
  });

  it("falls back to the generic message for a non-Error rejection", async () => {
    mockApi.on("get", "/risks/r1/mitigation-tasks", throwsString());
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    expect(screen.queryByText(EMPTY_STATE)).not.toBeInTheDocument();
  });

  it("still does not claim there are no tasks once the error is dismissed", async () => {
    mockApi.on("get", "/risks/r1/mitigation-tasks", offline());
    const { user } = renderPanel();
    const alert = await screen.findByRole("alert");
    await user.click(within(alert).getByRole("button"));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.queryByText(EMPTY_STATE)).not.toBeInTheDocument();
  });

  it("shows the empty state once a later load succeeds with no tasks", async () => {
    let fail = true;
    mockApi.on("get", "/risks/r1/mitigation-tasks", () => {
      if (fail) throw new TypeError("Failed to fetch");
      return [];
    });
    mockApi.on("post", "/risks/r1/mitigation-tasks", {});
    const { user } = renderPanel();
    await screen.findByRole("alert");

    fail = false;
    await user.click(screen.getByRole("button", { name: /Add task/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByRole("textbox", { name: /^Title/ }), "Enable MFA");
    await user.click(within(dialog).getByRole("button", { name: "Create task" }));
    expect(await screen.findByText(EMPTY_STATE)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("MitigationTasksPanel — failed writes that are not API errors", () => {
  beforeEach(() => {
    mockApi.on("get", "/risks/r1/mitigation-tasks", [OPEN, SCHEDULED]);
  });

  it("shows why a new task could not be created", async () => {
    mockApi.on("post", "/risks/r1/mitigation-tasks", offline());
    const { user } = renderPanel();
    await screen.findByText("Review access rights");
    await user.click(screen.getByRole("button", { name: /Add task/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByRole("textbox", { name: /^Title/ }), "Enable MFA");
    await user.click(within(dialog).getByRole("button", { name: "Create task" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
  });

  it("shows why an edit could not be saved", async () => {
    mockApi.on("patch", "/mitigation-tasks/t1", throwsString());
    const { user } = renderPanel();
    await screen.findByText("Review access rights");
    await user.click(button(rowOf("Review access rights"), "edit"));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });

  it("shows why a delete failed", async () => {
    mockApi.on("delete", "/mitigation-tasks/t1", offline());
    const { user } = renderPanel();
    await screen.findByText("Review access rights");
    await user.click(button(rowOf("Review access rights"), "delete"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
    expect(screen.getByText("Review access rights")).toBeInTheDocument();
  });

  it("shows why completing a cycle failed", async () => {
    mockApi.on("post", "/mitigation-tasks/t1/occurrences/o1/complete", offline());
    const { user } = renderPanel();
    await screen.findByText("Review access rights");
    await user.click(button(rowOf("Review access rights"), "check_circle"));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Mark done" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
  });

  it("shows why skipping a cycle failed", async () => {
    mockApi.on("post", "/mitigation-tasks/t1/occurrences/o1/skip", throwsString());
    const { user } = renderPanel();
    await screen.findByText("Review access rights");
    await user.click(button(rowOf("Review access rights"), "skip_next"));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Skip cycle" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });

  it("shows why Activate now failed", async () => {
    mockApi.on("post", "/mitigation-tasks/t2/occurrences/o2/promote", offline());
    const { user } = renderPanel();
    await screen.findByText("Quarterly DR test");
    await user.click(button(rowOf("Quarterly DR test"), "bolt"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
  });
});
