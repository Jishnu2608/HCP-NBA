import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Avatar, Truncate } from "./ui";

// Patient names arrive already masked from the server for readers who may not see them
// (G****** S*****). The client renders them as given and derives nothing beyond the masked
// string: initials come from the first characters, which the masked form keeps anyway.
describe("masked patient names", () => {
  it("renders a masked name verbatim and derives only its initials", () => {
    const { container } = render(
      <>
        <Avatar name="G****** S*****" />
        <Truncate>G****** S*****</Truncate>
      </>,
    );
    expect(container.textContent).toBe("GSG****** S*****");
  });
});
