import { expect, Page, test } from "@playwright/test";

function fakeJwt(role: "user" | "admin") {
  const encode = (value: object) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "none" })}.${encode({ sub: "tester", role })}.sig`;
}

async function mockBackend(page: Page) {
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/conversations" && request.method() === "GET") {
      return route.fulfill({ json: { conversations: [] } });
    }
    if (path === "/api/conversations" && request.method() === "POST") {
      return route.fulfill({
        status: 201,
        json: {
          id: 1,
          title: "新对话",
          skill_id: null,
          provider_id: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          messages: [],
        },
      });
    }
    if (path === "/api/conversations/1" && request.method() === "GET") {
      return route.fulfill({
        json: {
          id: 1,
          title: "新对话",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          messages: [],
        },
      });
    }
    if (path.endsWith("/messages/stream")) {
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          "data: 工作日加班按1.5倍支付。[1]\n\n",
          'data: [SOURCES][{"index":1,"source":"hr.md","content":"工作日加班按1.5倍支付。","score":0.1}]\n\n',
          "data: [DONE]\n\n",
        ].join(""),
      });
    }
    if (path === "/api/skills") return route.fulfill({ json: { skills: [] } });
    if (path === "/api/providers") return route.fulfill({ json: { providers: [] } });
    if (path === "/api/knowledge/status") {
      return route.fulfill({ json: { documents: 4, chunks: 20 } });
    }
    return route.fulfill({ status: 200, json: {} });
  });
}

async function openAs(page: Page, role: "user" | "admin") {
  await mockBackend(page);
  await page.addInitScript((token) => {
    localStorage.setItem("hermes_jwt", token);
  }, fakeJwt(role));
  await page.goto("/");
}

test("普通用户只能看到对话入口", async ({ page }) => {
  await openAs(page, "user");
  await expect(page.getByText("对话", { exact: true })).toBeVisible();
  await expect(page.getByText("文档管理", { exact: true })).toHaveCount(0);
  await expect(page.getByText("向量片段", { exact: true })).toHaveCount(0);
});

test("管理员可见知识库入口并能完成流式问答", async ({ page }) => {
  await openAs(page, "admin");
  await expect(page.getByText("文档管理", { exact: true })).toBeVisible();
  await expect(page.getByText("向量片段", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "新建对话" }).last().click();
  const input = page.getByPlaceholder("输入消息... (Enter 发送, Shift+Enter 换行)");
  await input.fill("工作日加班几倍？");
  await input.press("Enter");
  await expect(page.getByText(/工作日加班按1.5倍支付/)).toBeVisible();
  await page.getByRole("button", { name: "1 个参考来源" }).click();
  await expect(page.getByText(/hr.md/)).toBeVisible();
});
