/** P7.3 验证：pi 是否把 tool-result 的 image content 喂给视觉模型。
 *  registerTool 返回 {type:"image", data, mimeType} → 用真实 flash-vision 问，
 *  若模型答出图里的内容（绿底/红圆/蓝方块），则 pi 图像路由通。
 *  仅测试用，不入正式 ext。
 */
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";
import { readFileSync } from "node:fs";

const PNG = readFileSync("/tmp/vision-test.png").toString("base64");

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "vision_test",
    label: "Vision Test",
    description: "返回一张测试图，供验证视觉路由。",
    parameters: Type.Object({}),
    async execute() {
      return {
        content: [
          { type: "text", text: "下面是测试图。" },
          { type: "image", data: PNG, mimeType: "image/png" },
        ],
        details: {},
        isError: false,
      };
    },
  });
}
