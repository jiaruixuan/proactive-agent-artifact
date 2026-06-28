/**
 * User Operations
 *
 */

import { Operation } from "./base";

export class UserRequestOperation extends Operation {
	readonly role = "user";
	operationType: string = "user_request";
	readableDetails: string;
	request: string;

	constructor(request: string) {
		super();
		this.request = request;
		this.readableDetails = this.buildReadableDetails();
	}

	buildReadableDetails(): string {
		return `<note>
Below are the requirements directly provided by the user. These requirements are VERY IMPORTANT, and your future actions MUST fully take them into account.
Please note: if the user's request includes images, they will be replaced with [picture #x]. The actual image contents will then be appended sequentially at the end of this message.
</note>
<user_request>
${this.request}
</user_request>`;
	}
}
