template <typename T>
T f(T x) { return x + unknown_symbol; }
int main() {
    return f(1);
}
