/**
 * The Layered Dependency View (React Flow): a deep link centres the card, the
 * graph draws its neighbours and edges, and a neighbour opens the card panel.
 */
import { expect, gotoApp, test } from "./fixtures";
import { t } from "./i18n";

test("the dependency view centres a deep-linked card and opens a neighbour", async ({ page, demo, request }) => {
  await gotoApp(page, `/reports/dependencies?center=${demo.sapId}`);
  await expect(page.locator('button[value="c4"][aria-pressed="true"]')).toBeVisible();
  const centre = page.locator(`.react-flow__node[data-id="${demo.sapId}"]`);
  await expect(centre).toBeVisible();
  await expect.poll(() => page.locator(".react-flow__edge").count()).toBeGreaterThan(0);

  // Card nodes only: the four layer boxes are React Flow nodes as well. React
  // Flow's zoom controls float over the canvas, and where the layout puts a
  // neighbour depends on the demo data's order, so take one nothing covers:
  // the element at its centre must be the node itself.
  const uncoveredNeighbour = () =>
    page.evaluate((centreId) => {
      const nodes = document.querySelectorAll<HTMLElement>(
        `.react-flow__node-ldvNode:not([data-id="${centreId}"])`,
      );
      for (const node of nodes) {
        const box = node.getBoundingClientRect();
        const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
        if (hit && node.contains(hit)) return node.dataset.id ?? null;
      }
      return null;
    }, demo.sapId);
  await expect.poll(uncoveredNeighbour).not.toBeNull();
  const neighbourId = await uncoveredNeighbour();
  const neighbour = page.locator(`.react-flow__node[data-id="${neighbourId}"]`);
  await expect(neighbour).toBeVisible();
  // The node's text starts with its type icon glyph, so take the name from the card itself.
  const card = (await (await request.get(`/api/v1/cards/${neighbourId}`)).json()) as { name: string };
  await neighbour.click();
  await expect(page.getByRole("presentation").getByText(card.name, { exact: true }).first()).toBeVisible();
});

test("shows a card's alias under its name once the Alias line is ticked", async ({ page, demo, request }) => {
  // The demo landscape carries no aliases, so give the centred card one for
  // the length of this spec and take it away again at the end.
  const alias = `E2E-${Date.now().toString(36)}`;
  const set = await request.patch(`/api/v1/cards/${demo.sapId}`, { data: { alias } });
  expect(set.ok(), `PATCH /cards answered ${set.status()}`).toBeTruthy();
  try {
    await gotoApp(page, `/reports/dependencies?center=${demo.sapId}`);
    const centre = page.locator(`.react-flow__node[data-id="${demo.sapId}"]`);
    await expect(centre).toBeVisible();
    await expect(centre).not.toContainText(alias);

    await page.getByRole("button", { name: t("common:cardDisplay.showOnCard") }).click();
    await page.getByRole("menu").getByText(t("common:labels.alias"), { exact: true }).click();
    await page.keyboard.press("Escape");

    // Rendered as a detail line like the subtype: the label, then the value.
    await expect(centre).toContainText(`${t("common:labels.alias")}:`);
    await expect(centre).toContainText(alias);
  } finally {
    await request.patch(`/api/v1/cards/${demo.sapId}`, { data: { alias: null } });
  }
});
