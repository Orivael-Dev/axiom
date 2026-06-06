import { Component, type ErrorInfo, type ReactNode } from "react";

// Stops one panel's render error from blanking the whole app. Shows a small
// inline message instead, and logs the error for debugging.
export class ErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error(`[${this.props.label ?? "panel"}]`, error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="banner banner--error">
          ⚠ {this.props.label ?? "This panel"} hit an error: {this.state.error.message}
          <button className="btn btn--ghost" style={{ marginLeft: 10 }}
                  onClick={() => this.setState({ error: null })}>
            retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
