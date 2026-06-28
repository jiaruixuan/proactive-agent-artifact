
export interface IDisposable {
	dispose(): void;
}

export interface IOperation {
	readonly id: number;
	readonly operationType: string;
	readonly role: string;
	readonly startTimestamp: number;
	readonly endTimestamp: number;
	readonly readableDetails: string;
	tryMerge(operation: IOperation): boolean;
	toString(): string;
}

export type OperationCallback = (operation: IOperation) => void;

export interface ICollector extends IDisposable {
	readonly name: string;
	readonly isActive: boolean;
	start(): void;
	stop(): void;
	onOperation(callback: OperationCallback): IDisposable;
}

export interface IOperationFilter {
	types?: string[];
	roles?: string[];
	startTime?: number;
	endTime?: number;
	limit?: number;
	offset?: number;
}

export interface IStorage extends IDisposable {
	readonly name: string;
	write(operation: IOperation): void;
	query(filter?: IOperationFilter): IOperation[];
	flush(): Promise<void>;
}

export interface ICollectorManager extends IDisposable {
	register(collector: ICollector): void;
	unregister(name: string): void;
	get(name: string): ICollector | undefined;
	startAll(): void;
	stopAll(): void;
	onOperation(callback: OperationCallback): IDisposable;
}
