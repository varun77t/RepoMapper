import { createApp } from "./app";
import { shout } from "~lib/format";

function main() {
  const app = createApp("hello world");
  console.log(shout(app.text));
  return app;
}

main();
