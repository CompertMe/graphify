export function useNav() {
    const home = () => route('home')
    const isHome = () => route().current('home')

    const legacy = () => route('dup')

    return { home, isHome, legacy }
}
