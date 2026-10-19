export interface ConfirmState {
  status: "idle" | "confirming" | "editing" | "recorded";
  original: string;
  rewritten: string;
  draft: string;
  submitted?: string;
}

export type ConfirmEvent =
  | { type: "RECEIVE_CONFIRM"; original: string; rewritten: string }
  | { type: "EDIT" }
  | { type: "EDIT_CHANGE"; value: string }
  | { type: "CONFIRM" }
  | { type: "RECORD" }
  | { type: "RESET" };

export const initialConfirmState: ConfirmState = {
  status: "idle",
  original: "",
  rewritten: "",
  draft: "",
};

export function confirmReducer(state: ConfirmState, event: ConfirmEvent): ConfirmState {
  switch (event.type) {
    case "RECEIVE_CONFIRM":
      return {
        status: "confirming",
        original: event.original,
        rewritten: event.rewritten,
        draft: event.rewritten,
      };
    case "EDIT":
      return state.status === "confirming" ? { ...state, status: "editing" } : state;
    case "EDIT_CHANGE":
      return { ...state, draft: event.value };
    case "CONFIRM":
      return {
        ...initialConfirmState,
        submitted: state.status === "editing" ? state.draft : state.rewritten,
      };
    case "RECORD":
      return { ...state, status: "recorded" };
    case "RESET":
      return initialConfirmState;
    default:
      return state;
  }
}
