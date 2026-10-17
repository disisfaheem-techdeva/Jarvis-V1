"use client";

import { Printer } from "@phosphor-icons/react";
import { Button } from "../ui";

export function PrintButton() {
  return (
    <Button className="print:hidden" onClick={() => window.print()}>
      <Printer size={18} /> Print slip
    </Button>
  );
}
