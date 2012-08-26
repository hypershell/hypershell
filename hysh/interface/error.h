
#pragma once

struct hy_error_methods;

typedef uint64_t hy_error_code;

static const uint64_t hy_error_iid = 0x4ff19839fd3c889f;

#define hy_fatal_error_code             (0xb40d0959d1926000 | 500)
#define hy_failure_code                 (0xa3bcf95ff0bd1000 | 500)
#define hy_query_interface_error_code   (0xe2544e6d6a77e000 | 500)
#define hy_not_found_code               (0xf481b93aa59bf000 | 404)
#define hy_not_implemented_code         (0x967f026ed5aa1000 | 500)