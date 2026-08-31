use risc0_zkvm::guest::env;
use sha2::{Digest, Sha256};

/// hello-guest:读入字节 x,journal 输出 SHA256(x)(32 字节)。
fn main() {
    let x: Vec<u8> = env::read();
    let hash: [u8; 32] = Sha256::digest(&x).into();
    env::commit(&hash);
}
