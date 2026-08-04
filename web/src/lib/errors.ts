import type { DuplicateTransaction } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * The invoice being saved is already on file.
 *
 * Its own class because the caller does not just report it: it shows the user
 * the row that was matched and offers to save anyway, which needs the matched
 * row rather than only a message.
 */
export class DuplicateError extends ApiError {
  constructor(
    message: string,
    public duplicate: DuplicateTransaction,
  ) {
    super(message, 409);
    this.name = "DuplicateError";
  }
}
