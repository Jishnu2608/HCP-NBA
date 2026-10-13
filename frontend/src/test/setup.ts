// jsdom lacks a few browser APIs the components use (motion checks reduced motion).
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: query.includes("reduce"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

afterEach(() => cleanup());

// The sign-in page's ambient canvas: jsdom has no 2D context, and the page copes with none.
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement["getContext"];
