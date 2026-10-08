/**
 * SurveyBuilder, regression tests: a reopened draft keeps its "via relation"
 * narrowing, a draft whose target type is gone does not pass the Target step,
 * an in-place route change drops the previous survey's card chips, and the
 * preview step opens on its spinner rather than flashing "Load Preview".
 *
 * Same harness as `SurveyBuilder.part1.mutation.test.tsx`: the real router,
 * `CardPicker` stubbed to the list of cards it holds.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { useNavigate } from "react-router";

vi.mock("@/api/client", () => import("@/test/apiMock").then((m) => m.apiClientModule()));
vi.mock("@/hooks/useMetamodel", () => import("@/test/hooks").then((m) => m.useMetamodelModule()));
vi.mock("@/hooks/useDateFormat", () => import("@/test/hooks").then((m) => m.useDateFormatModule()));

type Picked = { id: string; name: string; type: string };
vi.mock("@/components/CardPicker", () => ({
  default: ({
    label,
    value,
    onChange,
  }: {
    label: string;
    value?: Picked[];
    onChange: (v: Picked[]) => void;
  }) => {
    const specific = label.startsWith("Search cards by name");
    return (
      <div data-testid={specific ? "specific-picker" : "related-picker"}>
        {(value ?? []).map((v, i) => (
          <span key={`${v.id}-${i}`}>{v.name}</span>
        ))}
        {!specific && (
          <>
            <button onClick={() => onChange([{ id: "org-9", name: "Picked Org", type: "Organization" }])}>
              related: Org
            </button>
            <button
              onClick={() =>
                onChange([
                  { id: "itc-1", name: "First ITC", type: "ITComponent" },
                  { id: "itc-2", name: "Second ITC", type: "ITComponent" },
                ])
              }
            >
              related: two ITCs
            </button>
            <button onClick={() => onChange([])}>related: none</button>
          </>
        )}
      </div>
    );
  },
}));

import SurveyBuilder from "./SurveyBuilder";
import { mockApi } from "@/test/apiMock";
import { hookState, withMetamodel } from "@/test/hooks";
import {
  makeCardType,
  makeField,
  makeOption,
  makeRelationType,
  makeSection,
} from "@/test/fixtures/metamodel";
import { renderWithProviders } from "@/test/render";

const APP = makeCardType({
  key: "Application",
  label: "Application",
  fields_schema: [
    makeSection({
      section: "General",
      fields: [
        makeField({
          key: "criticality",
          label: "Criticality",
          type: "single_select",
          options: [makeOption({ key: "high", label: "High" })],
        }),
      ],
    }),
  ],
});
const ORG = makeCardType({ key: "Organization", label: "Organization" });
const RETIRED = makeCardType({ key: "Retired", label: "Retired", is_hidden: true });

const ORG_OWNS_APP = makeRelationType({
  key: "relOrgToAppOwns",
  source_type_key: "Organization",
  target_type_key: "Application",
  label: "owns",
  reverse_label: "is owned by",
});
const APP_USED_BY_ORG = makeRelationType({
  key: "relAppToOrg",
  source_type_key: "Application",
  target_type_key: "Organization",
  label: "is used by",
  reverse_label: "uses",
});

const ROLES = [{ key: "responsible", label: "Business Owner", allowed_types: null, translations: {} }];

const PREVIEW = {
  total_cards: 1,
  total_matched: 1,
  skipped: [],
  total_users: 1,
  total_requests: 1,
  targets: [
    {
      card_id: "c1",
      card_name: "CRM",
      card_type: "Application",
      users: [{ user_id: "u1", display_name: "Ada", email: "a@x", roles: [] }],
    },
  ],
};

const SAVED = {
  id: "survey-7",
  name: "Saved",
  description: "",
  message: "",
  status: "draft",
  target_type_key: "Application",
  target_roles: ["responsible"],
  fields: [],
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function GoTo({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(to)}>go {to}</button>;
}

function renderBuilder(route = "/admin/surveys/new") {
  return renderWithProviders(
    <>
      <SurveyBuilder />
      <GoTo to="/admin/surveys/survey-8" />
    </>,
    {
      route,
      routes: [{ path: "/admin/surveys/new" }, { path: "/admin/surveys/:id" }],
    },
  );
}

const next = () => screen.getByRole("button", { name: /^Next/ });

function lastDraft(): Record<string, unknown> {
  const writes = mockApi.calls.filter(
    (c) =>
      (c.method === "post" && c.path === "/surveys") ||
      (c.method === "patch" && /^\/surveys\/[^/]+$/.test(c.path)),
  );
  return writes[writes.length - 1].body as Record<string, unknown>;
}

async function openDraft() {
  await waitFor(() => expect(screen.getByLabelText(/Survey Name/)).toHaveValue("Saved"));
}

beforeEach(() => {
  hookState.reset();
  mockApi.reset();
  withMetamodel([APP, ORG, RETIRED], [ORG_OWNS_APP, APP_USED_BY_ORG]);
  mockApi.on("get", "/tag-groups", []);
  mockApi.on("get", /^\/stakeholder-roles/, ROLES);
  mockApi.on("post", "/surveys", { id: "survey-1" });
  mockApi.on("patch", "/surveys/survey-1", {});
  mockApi.on("patch", "/surveys/survey-7", {});
  mockApi.on("patch", "/surveys/survey-8", {});
  mockApi.on("post", "/surveys/survey-1/preview", PREVIEW);
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("SurveyBuilder — a reopened draft's relation narrowing", () => {
  beforeEach(() => {
    mockApi.on("get", "/surveys/survey-7", {
      ...SAVED,
      target_filters: { related_ids: ["org-1"], relation_type_key: "relOrgToAppOwns" },
    });
    mockApi.on("get", "/cards/org-1", { id: "org-1", name: "Acme", type: "Organization" });
  });

  it("shows the stored relation on the Target step and saves it again", async () => {
    const { user } = renderBuilder("/admin/surveys/survey-7");
    await openDraft();
    await user.click(next());
    await screen.findByText("Target Cards");
    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /Via relation/ })).toHaveTextContent("is owned by");

    // The step change auto-saved it.
    await waitFor(() => expect(mockApi.callsOf("patch", "/surveys/survey-7")).toHaveLength(1));
    expect((lastDraft().target_filters as Record<string, unknown>).relation_type_key).toBe(
      "relOrgToAppOwns",
    );

    await user.click(screen.getByRole("button", { name: /Save Draft/ }));
    await waitFor(() => expect(mockApi.callsOf("patch", "/surveys/survey-7")).toHaveLength(2));
    expect(lastDraft().target_filters).toMatchObject({
      related_ids: ["org-1"],
      relation_type_key: "relOrgToAppOwns",
    });
  });

  it("keeps it while the related cards are still being looked up", async () => {
    const card = deferred<object>();
    mockApi.on("get", "/cards/org-1", () => card.promise);
    const { user } = renderBuilder("/admin/surveys/survey-7");
    await openDraft();

    await user.click(screen.getByRole("button", { name: /Save Draft/ }));
    await waitFor(() => expect(mockApi.callsOf("patch", "/surveys/survey-7")).toHaveLength(1));
    expect((lastDraft().target_filters as Record<string, unknown>).relation_type_key).toBe(
      "relOrgToAppOwns",
    );
    await act(async () => card.resolve({ id: "org-1", name: "Acme", type: "Organization" }));
  });
});

describe("SurveyBuilder — a draft whose target type is gone", () => {
  for (const [what, typeKey] of [
    ["removed from the metamodel", "Deleted"],
    ["hidden", "Retired"],
  ] as const) {
    it(`refuses to leave the Target step for a type ${what}`, async () => {
      mockApi.on("get", "/surveys/survey-7", { ...SAVED, target_type_key: typeKey, target_filters: {} });
      const { user } = renderBuilder("/admin/surveys/survey-7");
      await openDraft();
      await user.click(next());
      await screen.findByText("Target Cards");

      await user.click(next());
      expect(await screen.findByText("Please select a target card type")).toBeInTheDocument();
      expect(screen.getByText("Target Cards")).toBeInTheDocument();
      expect(screen.queryByText("Select Fields")).not.toBeInTheDocument();
    });
  }
});

describe("SurveyBuilder — the route changing in place", () => {
  it("drops the previous survey's specific and related card chips", async () => {
    mockApi.on("get", "/surveys/survey-7", {
      ...SAVED,
      target_filters: { card_ids: ["app-1"], related_ids: ["org-1"] },
    });
    mockApi.on("get", "/surveys/survey-8", {
      ...SAVED,
      id: "survey-8",
      name: "Second",
      target_filters: { card_ids: ["app-2"], related_ids: ["org-2"] },
    });
    mockApi.on("get", "/cards/app-1", { id: "app-1", name: "First App", type: "Application" });
    mockApi.on("get", "/cards/org-1", { id: "org-1", name: "First Org", type: "Organization" });
    mockApi.on("get", "/cards/app-2", { id: "app-2", name: "Second App", type: "Application" });
    mockApi.on("get", "/cards/org-2", { id: "org-2", name: "Second Org", type: "Organization" });
    const { user } = renderBuilder("/admin/surveys/survey-7");
    await openDraft();
    await user.click(next());
    expect(await screen.findByText("First App")).toBeInTheDocument();
    expect(await screen.findByText("First Org")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "go /admin/surveys/survey-8" }));
    expect(await screen.findByText("Second App")).toBeInTheDocument();
    expect(await screen.findByText("Second Org")).toBeInTheDocument();
    expect(screen.queryByText("First App")).not.toBeInTheDocument();
    expect(screen.queryByText("First Org")).not.toBeInTheDocument();
    // Exactly the new survey's chip in each picker, nothing left over.
    expect(screen.getByTestId("specific-picker").querySelectorAll("span")).toHaveLength(1);
    expect(screen.getByTestId("related-picker").querySelectorAll("span")).toHaveLength(1);
  });
});

describe("SurveyBuilder — narrowing picked related cards", () => {
  async function toTargetWithType(user: ReturnType<typeof renderBuilder>["user"]) {
    await user.type(await screen.findByLabelText(/Survey Name/), "Owner check");
    await user.click(next());
    await screen.findByText("Target Cards");
    await user.click(screen.getByRole("combobox", { name: /^Type/ }));
    await user.click(await screen.findByRole("option", { name: /Application/ }));
  }

  async function narrowToOwnedBy(user: ReturnType<typeof renderBuilder>["user"]) {
    await user.click(screen.getByText("related: Org"));
    await user.click(await screen.findByRole("combobox", { name: /Via relation/ }));
    await user.click(await screen.findByRole("option", { name: "is owned by" }));
    expect(screen.getByRole("combobox", { name: /Via relation/ })).toHaveTextContent("is owned by");
  }

  it("forgets the narrowing when the related cards are cleared", async () => {
    const { user } = renderBuilder();
    await toTargetWithType(user);
    await narrowToOwnedBy(user);

    await user.click(screen.getByText("related: none"));
    await waitFor(() =>
      expect(screen.queryByRole("combobox", { name: /Via relation/ })).not.toBeInTheDocument(),
    );
    // Picking the card again starts un-narrowed ("any relation" is the empty value).
    await user.click(screen.getByText("related: Org"));
    expect(await screen.findByRole("combobox", { name: /Via relation/ })).not.toHaveTextContent(
      "is owned by",
    );
    await user.click(screen.getByRole("button", { name: /Save Draft/ }));
    await waitFor(() => expect(mockApi.callsOf("post", "/surveys")).toHaveLength(1));
    expect((lastDraft().target_filters as Record<string, unknown>).relation_type_key).toBeUndefined();
  });

  it("drops the narrowing when several cards it cannot apply to replace the picked one", async () => {
    const { user } = renderBuilder();
    await toTargetWithType(user);
    await narrowToOwnedBy(user);

    await user.click(screen.getByText("related: two ITCs"));
    await user.click(screen.getByRole("button", { name: /Save Draft/ }));
    await waitFor(() => expect(mockApi.callsOf("post", "/surveys")).toHaveLength(1));
    const filters = lastDraft().target_filters as Record<string, unknown>;
    expect(filters.related_ids).toEqual(["itc-1", "itc-2"]);
    expect(filters.relation_type_key).toBeUndefined();
  });
});

describe("SurveyBuilder — entering the preview step", () => {
  it("opens on the spinner, never on the Load Preview button", async () => {
    const pending = deferred<typeof PREVIEW>();
    mockApi.on("post", "/surveys/survey-1/preview", () => pending.promise);
    const { user } = renderBuilder();
    await user.type(await screen.findByLabelText(/Survey Name/), "Owner check");
    await user.click(next());
    await screen.findByText("Target Cards");
    await user.click(screen.getByRole("combobox", { name: /^Type/ }));
    await user.click(await screen.findByRole("option", { name: /Application/ }));
    await user.click(screen.getByRole("checkbox", { name: /Business Owner/ }));
    await user.click(next());
    await screen.findByText("Select Fields");
    await user.click(screen.getByText("Criticality"));

    await user.click(next());
    await screen.findByText("Preview & Send", { selector: "h6" });
    expect(screen.queryByRole("button", { name: "Load Preview" })).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();

    await act(async () => pending.resolve(PREVIEW));
    expect(await screen.findByText("CRM")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load Preview" })).not.toBeInTheDocument();
  });
});
