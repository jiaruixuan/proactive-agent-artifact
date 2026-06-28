/**
 * Sanitization helpers for removing sensitive data from logs.
 */

/**
 * Sanitize sensitive data inside a string.
 */
export function sanitizeString(text: string): string {
	if (typeof text !== "string") {
		return text;
	}

	let sanitized = text;

	// Keep only the last two path segments.
	sanitized = sanitized.replace(/\/[^/]+\/[^/]+\/([^/]+\/[^/]+)$/g, ".../$1");
	sanitized = sanitized.replace(/\\[^\\]+\\[^\\]+\\([^\\]+\\[^\\]+)$/g, "...\\$1");

	// Keep short prefixes and suffixes for long identifiers.
	sanitized = sanitized.replace(/\b([a-zA-Z0-9]{3})[a-zA-Z0-9]{4,}([a-zA-Z0-9]{3})\b/g, "$1***$2");

	// Keep only the first UUID segment.
	sanitized = sanitized.replace(/([a-f0-9]{8})-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/gi, "$1-****");

	// Keep only a short token prefix.
	sanitized = sanitized.replace(/\b(ey[a-zA-Z0-9]{4})[a-zA-Z0-9+/=]{10,}/g, "$1***");

	return sanitized;
}

/**
 * Sanitize error-like values.
 */
export function sanitizeError(error: any): any {
	if (!error) {
		return error;
	}

	// Strings can be sanitized directly.
	if (typeof error === "string") {
		return sanitizeString(error);
	}

	// Preserve only sanitized Error fields.
	if (error instanceof Error) {
		const sanitized: any = {
			name: error.name,
			message: sanitizeString(error.message),
		};

		// Sanitize the stack when present.
		if (error.stack) {
			sanitized.stack = sanitizeString(error.stack);
		}

		return sanitized;
	}

	// Recursively sanitize plain objects.
	if (typeof error === "object") {
		return sanitizeObject(error);
	}

	return error;
}

/**
 * Sanitize sensitive fields inside an object.
 *
 * Sensitive fields are removed entirely from the output.
 */
export function sanitizeObject(obj: any): any {
	if (!obj || typeof obj !== "object") {
		return obj;
	}

	// Sanitize arrays element-by-element.
	if (Array.isArray(obj)) {
		return obj.map(item => sanitizeObject(item));
	}

	const sanitized: any = {};

	for (const key in obj) {
		if (!Object.prototype.hasOwnProperty.call(obj, key)) {
			continue;
		}

		const value = obj[key];

		// Sensitive fields are skipped instead of redacted in place.
		if (
			[
				"token",
				"ft_token",
				"password",
				"secret",
				"apiKey",
				"userId",
				"user_id",
				"sessionId",
				"session_id",
				"projectId",
				"project_id",
			].includes(key)
		) {
			continue;
		}

		// Recurse into nested values.
		if (typeof value === "string") {
			sanitized[key] = sanitizeString(value);
		} else if (typeof value === "object" && value !== null) {
			sanitized[key] = sanitizeObject(value);
		} else {
			sanitized[key] = value;
		}
	}

	return sanitized;
}

/**
 * Safe replacement for console.error.
 */
export function safeError(prefix: string, message: string, error?: any): void {
	if (error) {
		console.error(prefix, message, sanitizeError(error));
	} else {
		console.error(prefix, sanitizeString(message));
	}
}

/**
 * Safe replacement for console.warn.
 */
export function safeWarn(prefix: string, message: string, data?: any): void {
	if (data) {
		console.warn(prefix, sanitizeString(message), sanitizeObject(data));
	} else {
		console.warn(prefix, sanitizeString(message));
	}
}
