import * as React from "react";
import { shout } from "../lib/format";

export interface Props {
  label: string;
}

export class Widget extends React.Component<Props> {
  render() {
    return <div>{shout(this.props.label)}</div>;
  }
}

export const SmallWidget = (p: Props) => <span>{p.label}</span>;
