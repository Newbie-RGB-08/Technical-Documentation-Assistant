import { describe, expect, it } from "vitest";
import { confirmReducer, initialConfirmState } from "./confirmMachine";

const confirmingState = confirmReducer(initialConfirmState, {
  type: "RECEIVE_CONFIRM",
  original: "这个怎么配？",
  rewritten: "如何配置 PTDRA 的 MySQL 连接？",
});

describe("confirm 流程状态机", () => {
  it("RECEIVE_CONFIRM：idle → confirming，draft 初始化为改写问题", () => {
    expect(confirmingState.status).toBe("confirming");
    expect(confirmingState.original).toBe("这个怎么配？");
    expect(confirmingState.rewritten).toBe("如何配置 PTDRA 的 MySQL 连接？");
    expect(confirmingState.draft).toBe(confirmingState.rewritten);
  });

  it("confirming 状态直接 CONFIRM：提交改写问题并回到 idle", () => {
    const next = confirmReducer(confirmingState, { type: "CONFIRM" });
    expect(next.status).toBe("idle");
    expect(next.submitted).toBe("如何配置 PTDRA 的 MySQL 连接？");
  });

  it("EDIT 仅在 confirming 下生效", () => {
    const editing = confirmReducer(confirmingState, { type: "EDIT" });
    expect(editing.status).toBe("editing");
    // idle 状态下 EDIT 无效
    expect(confirmReducer(initialConfirmState, { type: "EDIT" })).toEqual(initialConfirmState);
  });

  it("editing → EDIT_CHANGE → CONFIRM：提交用户修改稿", () => {
    let state = confirmReducer(confirmingState, { type: "EDIT" });
    state = confirmReducer(state, { type: "EDIT_CHANGE", value: "如何配置 rag_meta 数据库连接？" });
    const done = confirmReducer(state, { type: "CONFIRM" });
    expect(done.submitted).toBe("如何配置 rag_meta 数据库连接？");
    expect(done.status).toBe("idle");
  });

  it("RECORD：保持内容，状态 → recorded", () => {
    const recorded = confirmReducer(confirmingState, { type: "RECORD" });
    expect(recorded.status).toBe("recorded");
    expect(recorded.original).toBe(confirmingState.original);
  });

  it("RESET：回到初始态", () => {
    const reset = confirmReducer(confirmingState, { type: "RESET" });
    expect(reset).toEqual(initialConfirmState);
  });
});
