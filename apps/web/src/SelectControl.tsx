import type { SelectHTMLAttributes } from "react";
import Icon from "./Icon";
import "./SelectControl.css";

export default function SelectControl(
  props: SelectHTMLAttributes<HTMLSelectElement>,
) {
  return (
    <span className="select-control">
      <select {...props} />
      <Icon name="chevronDown" className="select-chevron" />
    </span>
  );
}
