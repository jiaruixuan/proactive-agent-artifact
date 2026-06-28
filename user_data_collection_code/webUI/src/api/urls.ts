import { Env } from "@/utils/getEnv";
import { getLanguage } from "@/utils/getLanguage";

/**
 * FcUrl stores the service URLs used by the web UI.
 */
export const FcUrl = {
	serverURL: "https://assistant.example.com",
	codeuserURL: "", // .../codeuser
	privacyUrl: "", // .../codeuser/privacy
	agreementUrl: "", // .../codeuser/agreement
	wechatLoginBaseURL: "https://assistant.example.com/codeuser",
	wechatRedirectUri: encodeURIComponent("https://assistant.example.com/codeuser" + "/wx_login"),
};

if (Env.isDev) {
	FcUrl.serverURL = "https://assistant.example.com";
}

//  else if (panelState && panelState.type === "chat") {
// 	serverURL = panelState.serverURL || "";
// }
FcUrl.codeuserURL = FcUrl.serverURL + "/codeuser";
FcUrl.privacyUrl = FcUrl.codeuserURL + "/privacy";
FcUrl.agreementUrl = FcUrl.codeuserURL + "/agreenment";

const language = getLanguage();
if (!language.startsWith("zh")) {
	FcUrl.privacyUrl = FcUrl.codeuserURL + "/privacy_en";
	FcUrl.agreementUrl = FcUrl.codeuserURL + "/agreement_en";
	FcUrl.codeuserURL = FcUrl.codeuserURL.replace("assistant.example.com", "api.assistant.example.com");
}
